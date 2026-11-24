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
else
	VARIANT := $(notdir $(CC))
	CFLAGS := $(BASE_CFLAGS)
	LDFLAGS := $(SYS_LDFLAGS)
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

.PHONY: all static clean distclean test san install

PREFIX_BIN ?= $(HOME)/.local/bin

all: $(BIN)

static: $(BIN)

install: $(BIN)
	install -d $(PREFIX_BIN)
	install -m 755 $(BIN) $(PREFIX_BIN)/ccgram-c

$(BIN): $(OBJS) $(MAIN_OBJ)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

$(OBJ_DIR)/%.o: %.c include/ccgram.h
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
	rm -rf $(BUILD_DIR)/musl $(BUILD_DIR)/gcc $(BUILD_DIR)/clang $(BUILD_DIR)/cc $(BUILD_DIR)/asan $(BUILD_DIR)/tsan

distclean:
	rm -rf $(BUILD_DIR)
