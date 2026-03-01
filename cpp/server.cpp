#include <coroutine>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace {

constexpr const char* SERVICE_NAME = "xyz.openbmc_project.Calculator";
constexpr const char* OBJECT_PATH = "/xyz/openbmc_project/calculator";
constexpr const char* VARLINK_SERVICE = "org.varlink.service";
constexpr const char* SERVICE_INTERFACE_DESCRIPTION = R"(interface xyz.openbmc_project.Calculator

type State (
    lastResult: int,
    status: string,
    base: string,
    owner: string,
    objectPath: string,
    serviceName: string
)

method Multiply(x: int, y: ?int) -> (z: int)
method Divide(x: int, y: ?int) -> (z: int)
method Express() -> (z: string)
method Clear() -> ()
method GetState() -> (state: State)
method SetOwner(owner: string) -> ()

error DivisionByZero ()
error PermissionDenied ()
)";

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

struct State {
    int64_t lastResult{0};
    std::string status{"Success"};
    std::string base{"Decimal"};
    std::string owner{"root"};
};

class Calculator {
public:
    Task<int64_t> multiply_async(int64_t x, int64_t y) {
        std::lock_guard<std::mutex> lock(mu_);
        state_.lastResult = x * y;
        state_.status = "Success";
        co_return state_.lastResult;
    }

    Task<int64_t> divide_async(int64_t x, int64_t y) {
        std::lock_guard<std::mutex> lock(mu_);
        if (y == 0) {
            state_.status = "Error";
            throw std::runtime_error("DivisionByZero");
        }
        state_.lastResult = x / y;
        state_.status = "Success";
        co_return state_.lastResult;
    }

    Task<std::string> express_async() {
        std::lock_guard<std::mutex> lock(mu_);
        if (state_.base == "Binary") {
            co_return to_binary(state_.lastResult);
        }
        if (state_.base == "Heximal") {
            std::ostringstream os;
            os << std::hex << state_.lastResult;
            co_return os.str();
        }
        co_return std::to_string(state_.lastResult);
    }

    Task<std::string> clear_async() {
        std::lock_guard<std::mutex> lock(mu_);
        state_.lastResult = 0;
        state_.status = "Success";
        co_return std::string{"{}"};
    }

    Task<std::string> get_state_async() {
        std::lock_guard<std::mutex> lock(mu_);
        std::ostringstream os;
        os << "{\"lastResult\":" << state_.lastResult
           << ",\"status\":\"" << state_.status
           << "\",\"base\":\"" << state_.base
           << "\",\"owner\":\"" << state_.owner
           << "\",\"objectPath\":\"" << OBJECT_PATH
           << "\",\"serviceName\":\"" << SERVICE_NAME << "\"}";
        co_return os.str();
    }

    Task<std::string> set_owner_async(const std::string& owner) {
        const char* allow = std::getenv("CALCULATOR_ALLOW_OWNER_CHANGE");
        if (!allow || std::string(allow) != "1") {
            std::lock_guard<std::mutex> lock(mu_);
            state_.status = "Error";
            throw std::runtime_error("PermissionDenied");
        }
        std::lock_guard<std::mutex> lock(mu_);
        state_.owner = owner;
        state_.status = "Success";
        co_return std::string{"{}"};
    }

private:
    static std::string to_binary(int64_t v) {
        if (v == 0) {
            return "0";
        }
        std::string out;
        while (v > 0) {
            out.insert(out.begin(), static_cast<char>('0' + (v & 1)));
            v >>= 1;
        }
        return out;
    }

    std::mutex mu_;
    State state_;
};

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

std::optional<std::string> extract_method(const std::string& body) {
    return extract_string(body, "method");
}

std::string make_error(const std::string& err) {
    return "{\"error\":\"" + err + "\"}";
}

std::string handle_request(Calculator& calc, const std::string& line) {
    auto methodOpt = extract_method(line);
    if (!methodOpt) {
        return make_error("org.varlink.service.InvalidParameter");
    }

    const auto& method = *methodOpt;
    try {
        if (method == std::string(VARLINK_SERVICE) + ".GetInfo") {
            return "{\"parameters\":{\"vendor\":\"GoVarlink\",\"product\":\"" +
                   std::string(SERVICE_NAME) +
                   "\",\"version\":\"0.1.0\",\"url\":\"https://github.com/JohnBlue-git/GoVarlink\",\"interfaces\":[\"" +
                   std::string(SERVICE_NAME) + "\"]}}";
        }
        if (method == std::string(VARLINK_SERVICE) + ".GetInterfaceDescription") {
            auto iface = extract_string(line, "interface");
            if (!iface) {
                return make_error("org.varlink.service.InvalidParameter");
            }
            if (*iface != SERVICE_NAME) {
                return make_error("org.varlink.service.InterfaceNotFound");
            }

            std::string desc = SERVICE_INTERFACE_DESCRIPTION;
            std::string escaped;
            escaped.reserve(desc.size() + 64);
            for (char c : desc) {
                if (c == '\\' || c == '"') {
                    escaped.push_back('\\');
                    escaped.push_back(c);
                } else if (c == '\n') {
                    escaped += "\\n";
                } else {
                    escaped.push_back(c);
                }
            }
            return "{\"parameters\":{\"description\":\"" + escaped + "\"}}";
        }
        if (method == std::string(SERVICE_NAME) + ".Multiply") {
            auto x = extract_int(line, "x");
            auto y = extract_int(line, "y");
            if (!x) {
                return make_error("org.varlink.service.InvalidParameter");
            }
            int64_t result = calc.multiply_async(*x, y.value_or(1)).get();
            return "{\"parameters\":{\"z\":" + std::to_string(result) + "}}";
        }
        if (method == std::string(SERVICE_NAME) + ".Divide") {
            auto x = extract_int(line, "x");
            auto y = extract_int(line, "y");
            if (!x) {
                return make_error("org.varlink.service.InvalidParameter");
            }
            int64_t result = calc.divide_async(*x, y.value_or(1)).get();
            return "{\"parameters\":{\"z\":" + std::to_string(result) + "}}";
        }
        if (method == std::string(SERVICE_NAME) + ".Express") {
            std::string result = calc.express_async().get();
            return "{\"parameters\":{\"z\":\"" + result + "\"}}";
        }
        if (method == std::string(SERVICE_NAME) + ".Clear") {
            calc.clear_async().get();
            return "{\"parameters\":{}}";
        }
        if (method == std::string(SERVICE_NAME) + ".GetState") {
            std::string stateJson = calc.get_state_async().get();
            return "{\"parameters\":{\"state\":" + stateJson + "}}";
        }
        if (method == std::string(SERVICE_NAME) + ".SetOwner") {
            auto owner = extract_string(line, "owner");
            if (!owner) {
                return make_error("org.varlink.service.InvalidParameter");
            }
            calc.set_owner_async(*owner).get();
            return "{\"parameters\":{}}";
        }
        return make_error("org.varlink.service.MethodNotImplemented");
    } catch (const std::runtime_error& e) {
        return make_error(std::string(SERVICE_NAME) + "." + e.what());
    } catch (...) {
        return make_error("org.varlink.service.InternalError");
    }
}

void serve_connection(int fd, Calculator& calc) {
    std::string buffer;
    buffer.reserve(2048);
    char chunk[512];

    while (true) {
        ssize_t n = ::read(fd, chunk, sizeof(chunk));
        if (n <= 0) {
            break;
        }
        buffer.append(chunk, chunk + n);

        size_t pos = 0;
        while (true) {
            size_t eolNewline = buffer.find('\n', pos);
            size_t eolNul = buffer.find('\0', pos);
            size_t eol = std::string::npos;
            char delimiter = '\n';

            if (eolNewline == std::string::npos && eolNul == std::string::npos) {
                eol = std::string::npos;
            } else if (eolNewline == std::string::npos) {
                eol = eolNul;
                delimiter = '\0';
            } else if (eolNul == std::string::npos) {
                eol = eolNewline;
                delimiter = '\n';
            } else if (eolNul < eolNewline) {
                eol = eolNul;
                delimiter = '\0';
            } else {
                eol = eolNewline;
                delimiter = '\n';
            }

            if (eol == std::string::npos) {
                buffer.erase(0, pos);
                break;
            }
            std::string line = buffer.substr(pos, eol - pos);
            pos = eol + 1;

            std::string response = handle_request(calc, line);
            response.push_back(delimiter);
            (void)::write(fd, response.data(), response.size());
        }
    }

    ::close(fd);
}

}  // namespace

int main(int argc, char* argv[]) {
    std::string socketPath = "/tmp/calculator-cpp.sock";
    for (int i = 1; i < argc - 1; ++i) {
        if (std::string(argv[i]) == "--socket") {
            socketPath = argv[i + 1];
        }
    }

    if (std::filesystem::exists(socketPath)) {
        std::filesystem::remove(socketPath);
    }

    int serverFd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (serverFd < 0) {
        std::perror("socket");
        return 1;
    }

    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    std::snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", socketPath.c_str());

    if (::bind(serverFd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        std::perror("bind");
        ::close(serverFd);
        return 1;
    }

    if (::listen(serverFd, 32) < 0) {
        std::perror("listen");
        ::close(serverFd);
        return 1;
    }

    std::cout << "C++ calculator server on " << socketPath << std::endl;
    Calculator calculator;

    while (true) {
        int clientFd = ::accept(serverFd, nullptr, nullptr);
        if (clientFd < 0) {
            continue;
        }
        std::thread([clientFd, &calculator] { serve_connection(clientFd, calculator); }).detach();
    }
}