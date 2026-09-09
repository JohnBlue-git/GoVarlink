#include <chrono>
#include <coroutine>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace {

constexpr const char* SERVICE_NAME = "xyz.openbmc_project.Calculator";

template <typename T>
class Task {
public:
    struct promise_type {
        T value{};
        std::exception_ptr exception{};

        Task get_return_object() {
            return Task{std::coroutine_handle<promise_type>::from_promise(*this)};
        }
        std::suspend_never initial_suspend() noexcept { return {}; }
        std::suspend_always final_suspend() noexcept { return {}; }
        void return_value(T v) { value = std::move(v); }
        void unhandled_exception() { exception = std::current_exception(); }
    };

    explicit Task(std::coroutine_handle<promise_type> handle) : handle_(handle) {}
    Task(Task&& other) noexcept : handle_(other.handle_) { other.handle_ = nullptr; }
    Task(const Task&) = delete;
    Task& operator=(const Task&) = delete;
    ~Task() {
        if (handle_) {
            handle_.destroy();
        }
    }

    T get() {
        if (handle_.promise().exception) {
            std::rethrow_exception(handle_.promise().exception);
        }
        return std::move(handle_.promise().value);
    }

private:
    std::coroutine_handle<promise_type> handle_;
};

std::optional<std::string> extract_string(const std::string& body, const std::string& key) {
    auto pos = body.find("\"" + key + "\"");
    if (pos == std::string::npos) {
        return std::nullopt;
    }
    pos = body.find(':', pos);
    if (pos == std::string::npos) {
        return std::nullopt;
    }
    pos = body.find('"', pos);
    if (pos == std::string::npos) {
        return std::nullopt;
    }
    ++pos;
    size_t end = body.find('"', pos);
    if (end == std::string::npos) {
        return std::nullopt;
    }
    return body.substr(pos, end - pos);
}

std::optional<int64_t> extract_int(const std::string& body, const std::string& key) {
    auto pos = body.find("\"" + key + "\"");
    if (pos == std::string::npos) {
        return std::nullopt;
    }
    pos = body.find(':', pos);
    if (pos == std::string::npos) {
        return std::nullopt;
    }
    ++pos;
    while (pos < body.size() && std::isspace(static_cast<unsigned char>(body[pos]))) {
        ++pos;
    }
    size_t end = pos;
    if (end < body.size() && body[end] == '-') {
        ++end;
    }
    while (end < body.size() && std::isdigit(static_cast<unsigned char>(body[end]))) {
        ++end;
    }
    if (end == pos) {
        return std::nullopt;
    }
    try {
        return std::stoll(body.substr(pos, end - pos));
    } catch (...) {
        return std::nullopt;
    }
}

class Client {
public:
    explicit Client(std::string socket_path) : socket_path_(std::move(socket_path)) {}

    void connect_socket() {
        fd_ = ::socket(AF_UNIX, SOCK_STREAM, 0);
        if (fd_ < 0) {
            throw std::runtime_error("socket failed");
        }
        sockaddr_un addr{};
        addr.sun_family = AF_UNIX;
        std::snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", socket_path_.c_str());
        if (::connect(fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
            ::close(fd_);
            throw std::runtime_error("connect failed");
        }
    }

    ~Client() {
        if (fd_ >= 0) {
            ::close(fd_);
        }
    }

    Task<int64_t> multiply_async(int64_t x, int64_t y) {
        std::string req = "{\"method\":\"" + std::string(SERVICE_NAME) +
                          ".Multiply\",\"parameters\":{\"x\":" + std::to_string(x) +
                          ",\"y\":" + std::to_string(y) + "}}\n";
        auto res = call(req);
        auto err = extract_string(res, "error");
        if (err) {
            throw std::runtime_error(*err);
        }
        auto value = extract_int(res, "z");
        if (!value) {
            throw std::runtime_error("invalid response");
        }
        co_return *value;
    }

    Task<int64_t> divide_async(int64_t x, int64_t y) {
        std::string req = "{\"method\":\"" + std::string(SERVICE_NAME) +
                          ".Divide\",\"parameters\":{\"x\":" + std::to_string(x) +
                          ",\"y\":" + std::to_string(y) + "}}\n";
        auto res = call(req);
        auto err = extract_string(res, "error");
        if (err) {
            throw std::runtime_error(*err);
        }
        auto value = extract_int(res, "z");
        if (!value) {
            throw std::runtime_error("invalid response");
        }
        co_return *value;
    }

    Task<std::string> express_async() {
        std::string req = "{\"method\":\"" + std::string(SERVICE_NAME) + ".Express\",\"parameters\":{}}\n";
        auto res = call(req);
        auto err = extract_string(res, "error");
        if (err) {
            throw std::runtime_error(*err);
        }
        auto value = extract_string(res, "z");
        if (!value) {
            throw std::runtime_error("invalid response");
        }
        co_return *value;
    }

private:
    std::string call(const std::string& req) {
        if (::write(fd_, req.data(), req.size()) < 0) {
            throw std::runtime_error("write failed");
        }
        std::string out;
        char c;
        while (true) {
            ssize_t n = ::read(fd_, &c, 1);
            if (n <= 0) {
                throw std::runtime_error("read failed");
            }
            if (c == '\n') {
                break;
            }
            out.push_back(c);
        }
        return out;
    }

    int fd_{-1};
    std::string socket_path_;
};

}  // namespace

int main(int argc, char* argv[]) {
    std::string socket = "/tmp/calculator-cpp.sock";
    std::string method = "Multiply";
    int64_t x = 7;
    int64_t y = 3;
    int iterations = 1;

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--socket" && i + 1 < argc) {
            socket = argv[++i];
        } else if (arg == "--method" && i + 1 < argc) {
            method = argv[++i];
        } else if (arg == "--x" && i + 1 < argc) {
            x = std::stoll(argv[++i]);
        } else if (arg == "--y" && i + 1 < argc) {
            y = std::stoll(argv[++i]);
        } else if (arg == "--iterations" && i + 1 < argc) {
            iterations = std::stoi(argv[++i]);
        }
    }

    try {
        Client cli(socket);
        cli.connect_socket();

        auto start = std::chrono::steady_clock::now();
        if (method == "Multiply") {
            int64_t value = 0;
            for (int i = 0; i < iterations; ++i) {
                value = cli.multiply_async(x, y).get();
            }
            auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start);
            std::cout << "result=" << value << " elapsed_ms=" << elapsed.count()
                      << " iterations=" << iterations << std::endl;
            return 0;
        }
        if (method == "Divide") {
            int64_t value = 0;
            for (int i = 0; i < iterations; ++i) {
                value = cli.divide_async(x, y).get();
            }
            auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start);
            std::cout << "result=" << value << " elapsed_ms=" << elapsed.count()
                      << " iterations=" << iterations << std::endl;
            return 0;
        }
        if (method == "Express") {
            std::string value;
            for (int i = 0; i < iterations; ++i) {
                value = cli.express_async().get();
            }
            auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start);
            std::cout << "result=" << value << " elapsed_ms=" << elapsed.count()
                      << " iterations=" << iterations << std::endl;
            return 0;
        }
        std::cerr << "Unsupported method" << std::endl;
        return 2;
    } catch (const std::exception& e) {
        std::cerr << e.what() << std::endl;
        return 1;
    }
}