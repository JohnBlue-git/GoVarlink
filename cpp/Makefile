CXX ?= g++
CXXFLAGS ?= -std=c++20 -O2 -Wall -Wextra -pedantic
LDFLAGS ?=

BUILD_DIR := build
SERVER_SRC := server.cpp
CLIENT_SRC := client.cpp
SERVER_BIN := $(BUILD_DIR)/calculator_server
CLIENT_BIN := $(BUILD_DIR)/calculator_client

.PHONY: all clean

all: $(SERVER_BIN) $(CLIENT_BIN)

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(SERVER_BIN): $(SERVER_SRC) | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $< -o $@ $(LDFLAGS)

$(CLIENT_BIN): $(CLIENT_SRC) | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $< -o $@ $(LDFLAGS)

clean:
	rm -rf $(BUILD_DIR)
