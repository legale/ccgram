ifeq ($(origin CC),default)
	CC := musl-gcc
endif
SAN_CC ?= gcc

BUILD_DIR := build
MUSL_DEPS := $(BUILD_DIR)/deps/musl

BASE_CFLAGS := -std=c11 -D_POSIX_C_SOURCE=200809L -D_XOPEN_SOURCE=700 -Wall -Wextra -Werror -O2 -Iinclude
SYS_LDFLAGS := -lcurl -ljson-c -lz -lpthread -lm

ifneq (,$(findstring musl,$(CC)))
	VARIANT := musl
	CFLAGS := $(BASE_CFLAGS) -I$(MUSL_DEPS)/include
	LDFLAGS := -static -L$(MUSL_DEPS)/lib -lcurl -ljson-c -lmbedtls -lmbedx509 -lmbedcrypto -leverest -lp256m -lz -lpthread -lm
	DEPS_STAMP := $(MUSL_DEPS)/lib/libcurl.a
else
	VARIANT := $(notdir $(CC))
	CFLAGS := $(BASE_CFLAGS)
	LDFLAGS := $(SYS_LDFLAGS)
	DEPS_STAMP :=
endif

OBJ_DIR := $(BUILD_DIR)/$(VARIANT)/obj
BIN := $(BUILD_DIR)/$(VARIANT)/ccgram
TEST_BIN := $(BUILD_DIR)/$(VARIANT)/test_unit
ASAN_BIN := $(BUILD_DIR)/asan/test_unit
TSAN_BIN := $(BUILD_DIR)/tsan/test_unit

SRCS := \
	src/util.c \
	src/config.c \
	src/router.c \
	src/tmux.c \
	src/entities.c \
	src/screenshot.c \
	src/tg_api.c \
	src/diff.c \
	src/live.c \
	src/handlers.c

OBJS := $(patsubst %.c,$(OBJ_DIR)/%.o,$(SRCS))
MAIN_OBJ := $(OBJ_DIR)/src/main.o
TEST_OBJ := $(OBJ_DIR)/tests/test_unit.o

.PHONY: all static clean test san install deps

PREFIX_BIN ?= $(HOME)/.local/bin

all: $(BIN)

static: $(BIN)

deps: $(MUSL_DEPS)/lib/libcurl.a

$(MUSL_DEPS)/lib/libcurl.a:
	@set -e; \
	SYSROOT="$$(cd .. && pwd)/c/$(MUSL_DEPS)"; \
	SRCDIR="$$(cd .. && pwd)/c/$(BUILD_DIR)/deps/src"; \
	mkdir -p "$$SRCDIR" "$$SYSROOT"; \
	cd "$$SRCDIR" && \
	curl -fsSL https://github.com/madler/zlib/releases/download/v1.3.1/zlib-1.3.1.tar.gz -o zlib.tar.gz && \
	tar xzf zlib.tar.gz && \
	(cd zlib-1.3.1 && CC=musl-gcc ./configure --static --prefix="$$SYSROOT" && $(MAKE) -j$$(nproc) && $(MAKE) install) && \
	curl -fsSL https://github.com/Mbed-TLS/mbedtls/releases/download/mbedtls-3.6.2/mbedtls-3.6.2.tar.bz2 -o mbedtls.tar.bz2 && \
	tar xjf mbedtls.tar.bz2 && \
	(cd mbedtls-3.6.2 && cmake -B build -DCMAKE_C_COMPILER=musl-gcc -DCMAKE_INSTALL_PREFIX="$$SYSROOT" -DUSE_SHARED_MBEDTLS_LIBRARY=OFF -DUSE_STATIC_MBEDTLS_LIBRARY=ON -DENABLE_TESTING=OFF -DENABLE_PROGRAMS=OFF && cmake --build build -j$$(nproc) && cmake --install build) && \
	curl -fsSL https://curl.se/download/curl-8.11.0.tar.xz -o curl.tar.xz && \
	tar xJf curl.tar.xz && \
	(cd curl-8.11.0 && CC=musl-gcc PKG_CONFIG_PATH="$$SYSROOT/lib/pkgconfig" CPPFLAGS="-I$$SYSROOT/include" LDFLAGS="-L$$SYSROOT/lib" ./configure --prefix="$$SYSROOT" --disable-shared --enable-static --with-mbedtls="$$SYSROOT" --with-zlib="$$SYSROOT" --without-libpsl --without-nghttp2 --without-brotli --without-zstd --without-libidn2 --without-librtmp --without-libssh2 --disable-ldap --disable-ldaps --disable-rtsp --disable-dict --disable-telnet --disable-tftp --disable-pop3 --disable-imap --disable-smb --disable-smtp --disable-gopher --disable-mqtt --disable-ftp --disable-file --disable-docs --disable-manual --with-ca-bundle=/etc/ssl/certs/ca-certificates.crt --with-ca-path=/etc/ssl/certs && $(MAKE) -j$$(nproc) && $(MAKE) install) && \
	curl -fsSL https://s3.amazonaws.com/json-c_releases/releases/json-c-0.18.tar.gz -o json-c.tar.gz && \
	tar xzf json-c.tar.gz && \
	(cd json-c-0.18 && cmake -B build -DCMAKE_C_COMPILER=musl-gcc -DCMAKE_INSTALL_PREFIX="$$SYSROOT" -DBUILD_SHARED_LIBS=OFF -DBUILD_APPS=OFF -DBUILD_TESTING=OFF -DDISABLE_WERROR=ON && cmake --build build -j$$(nproc) && cmake --install build)

install: $(BIN)
	install -d $(PREFIX_BIN)
	install -m 755 $(BIN) $(PREFIX_BIN)/ccgram-c

$(BIN): $(OBJS) $(MAIN_OBJ)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

$(OBJ_DIR)/%.o: %.c include/ccgram.h $(DEPS_STAMP)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c -o $@ $<

test: $(OBJS) $(TEST_OBJ)
	@mkdir -p $(dir $(TEST_BIN))
	$(CC) $(CFLAGS) -o $(TEST_BIN) $^ $(LDFLAGS)
	./$(TEST_BIN)

san:
	@mkdir -p $(dir $(ASAN_BIN)) $(dir $(TSAN_BIN))
	$(SAN_CC) $(BASE_CFLAGS) -g -O1 -fsanitize=address,undefined -fno-omit-frame-pointer \
		-o $(ASAN_BIN) $(SRCS) tests/test_unit.c $(SYS_LDFLAGS)
	./$(ASAN_BIN)
	$(SAN_CC) $(BASE_CFLAGS) -g -O1 -fsanitize=thread \
		-o $(TSAN_BIN) $(SRCS) tests/test_unit.c $(SYS_LDFLAGS)
	./$(TSAN_BIN)

clean:
	rm -rf $(BUILD_DIR)
