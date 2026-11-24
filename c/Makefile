CC ?= cc
SAN_CC ?= cc
CFLAGS := -std=c11 -D_POSIX_C_SOURCE=200809L -D_XOPEN_SOURCE=700 -Wall -Wextra -Werror -O2 -Iinclude
LDFLAGS := -lcurl -ljson-c -lz -lpthread -lm

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

OBJS := $(SRCS:.c=.o)
MAIN_OBJ := src/main.o
TEST_OBJ := tests/test_unit.o

.PHONY: all clean test san install

PREFIX_BIN ?= $(HOME)/.local/bin

all: ccgram

install: ccgram
	install -d $(PREFIX_BIN)
	install -m 755 ccgram $(PREFIX_BIN)/ccgram-c

ccgram: $(OBJS) $(MAIN_OBJ)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

%.o: %.c include/ccgram.h
	$(CC) $(CFLAGS) -c -o $@ $<

test: $(OBJS) $(TEST_OBJ)
	$(CC) $(CFLAGS) -o test_unit $^ $(LDFLAGS)
	./test_unit

san:
	$(SAN_CC) $(CFLAGS) -g -O1 -fsanitize=address,undefined -fno-omit-frame-pointer \
		-o test_unit_san $(SRCS) tests/test_unit.c $(LDFLAGS)
	./test_unit_san
	rm -f test_unit_san

clean:
	rm -f $(OBJS) $(MAIN_OBJ) $(TEST_OBJ) ccgram test_unit test_unit_san
