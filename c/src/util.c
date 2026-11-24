#include "ccgram.h"

#include <ctype.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

/* ── String Buffer ─────────────────────────────────────────────────── */

void sb_init(struct strbuf *sb)
{
	sb->data = NULL;
	sb->len = 0;
	sb->cap = 0;
}

void sb_free(struct strbuf *sb)
{
	free(sb->data);
	sb->data = NULL;
	sb->len = 0;
	sb->cap = 0;
}

bool sb_reserve(struct strbuf *sb, size_t extra)
{
	size_t need = sb->len + extra + 1;
	size_t new_cap;
	char *p;

	if (need <= sb->cap)
		return true;

	new_cap = sb->cap ? sb->cap * 2 : 256;
	while (new_cap < need)
		new_cap *= 2;

	p = realloc(sb->data, new_cap);
	if (!p)
		return false;

	sb->data = p;
	sb->cap = new_cap;
	return true;
}

bool sb_append(struct strbuf *sb, const char *s, size_t n)
{
	if (!sb_reserve(sb, n))
		return false;
	if (n > 0)
		memcpy(sb->data + sb->len, s, n);
	sb->len += n;
	sb->data[sb->len] = '\0';
	return true;
}

bool sb_append_str(struct strbuf *sb, const char *s)
{
	return sb_append(sb, s, s ? strlen(s) : 0);
}

bool sb_appendf(struct strbuf *sb, const char *fmt, ...)
{
	va_list ap, ap2;
	int needed;

	va_start(ap, fmt);
	va_copy(ap2, ap);
	needed = vsnprintf(NULL, 0, fmt, ap);
	va_end(ap);

	if (needed < 0) {
		va_end(ap2);
		return false;
	}
	if (!sb_reserve(sb, (size_t)needed)) {
		va_end(ap2);
		return false;
	}
	vsnprintf(sb->data + sb->len, (size_t)needed + 1, fmt, ap2);
	va_end(ap2);
	sb->len += (size_t)needed;
	return true;
}

char *sb_detach(struct strbuf *sb)
{
	char *res = sb->data ? sb->data : xstrdup("");
	sb->data = NULL;
	sb->len = 0;
	sb->cap = 0;
	return res;
}

/* ── Logging ───────────────────────────────────────────────────────── */

static enum log_level g_log_lvl = LOG_INFO;
static pthread_mutex_t g_log_mu = PTHREAD_MUTEX_INITIALIZER;

struct throttle_entry {
	char key[128];
	double last_ts;
	int suppressed;
};

#define MAX_THROTTLE 128
static struct throttle_entry g_throttle[MAX_THROTTLE];
static size_t g_throttle_count = 0;

void log_set_level(enum log_level lvl)
{
	g_log_lvl = lvl;
}

static void log_vprint(enum log_level lvl, const char *fmt, va_list ap)
{
	static const char *const names[] = { "DEBUG", "INFO", "WARN", "ERROR" };
	struct timespec ts;
	struct tm tm;
	char tbuf[32];

	if (lvl < g_log_lvl)
		return;

	clock_gettime(CLOCK_REALTIME, &ts);
	localtime_r(&ts.tv_sec, &tm);
	strftime(tbuf, sizeof(tbuf), "%Y-%m-%d %H:%M:%S", &tm);

	pthread_mutex_lock(&g_log_mu);
	fprintf(stderr, "%s [%s] ", tbuf, names[lvl]);
	vfprintf(stderr, fmt, ap);
	fputc('\n', stderr);
	fflush(stderr);
	pthread_mutex_unlock(&g_log_mu);
}

void log_msg(enum log_level lvl, const char *fmt, ...)
{
	va_list ap;

	if (lvl < g_log_lvl)
		return;
	va_start(ap, fmt);
	log_vprint(lvl, fmt, ap);
	va_end(ap);
}

void log_throttled(const char *key, double interval_s, const char *fmt, ...)
{
	double now = mono_now();
	struct throttle_entry *e = NULL;
	va_list ap;

	pthread_mutex_lock(&g_log_mu);
	for (size_t i = 0; i < g_throttle_count; i++) {
		if (strcmp(g_throttle[i].key, key) == 0) {
			e = &g_throttle[i];
			break;
		}
	}
	if (!e && g_throttle_count < MAX_THROTTLE) {
		e = &g_throttle[g_throttle_count++];
		snprintf(e->key, sizeof(e->key), "%s", key);
		e->last_ts = 0.0;
		e->suppressed = 0;
	}
	if (e && e->last_ts > 0.0 && (now - e->last_ts) < interval_s) {
		e->suppressed++;
		pthread_mutex_unlock(&g_log_mu);
		return;
	}
	if (e) {
		e->last_ts = now;
		e->suppressed = 0;
	}
	pthread_mutex_unlock(&g_log_mu);

	va_start(ap, fmt);
	log_vprint(LOG_WARN, fmt, ap);
	va_end(ap);
}

void log_throttle_reset(const char *key)
{
	pthread_mutex_lock(&g_log_mu);
	for (size_t i = 0; i < g_throttle_count; i++) {
		if (strcmp(g_throttle[i].key, key) == 0) {
			g_throttle[i] = g_throttle[--g_throttle_count];
			break;
		}
	}
	pthread_mutex_unlock(&g_log_mu);
}

void log_throttle_sweep(void)
{
	double now = mono_now();

	pthread_mutex_lock(&g_log_mu);
	for (size_t i = 0; i < g_throttle_count;) {
		if ((now - g_throttle[i].last_ts) > 600.0)
			g_throttle[i] = g_throttle[--g_throttle_count];
		else
			i++;
	}
	pthread_mutex_unlock(&g_log_mu);
}

/* ── Time & Misc ───────────────────────────────────────────────────── */

double mono_now(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

uint64_t time_ns_now(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_REALTIME, &ts);
	return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

char *xstrdup(const char *s)
{
	size_t n;
	char *p;

	if (!s)
		return NULL;
	n = strlen(s);
	p = malloc(n + 1);
	if (p)
		memcpy(p, s, n + 1);
	return p;
}

/* Fast 128-bit FNV-1a-based hex digest for content change detection */
void content_hash_hex(const char *data, size_t len, char out[33])
{
	uint64_t h1 = 0xcbf29ce484222325ULL;
	uint64_t h2 = 0x84222325cbf29ce4ULL;

	for (size_t i = 0; i < len; i++) {
		uint8_t c = (uint8_t)data[i];
		h1 ^= c;
		h1 *= 0x100000001b3ULL;
		h2 ^= (uint8_t)(c ^ (i & 0xff));
		h2 *= 0x100000001b3ULL;
	}
	snprintf(out, 33, "%016llx%016llx", (unsigned long long)h1, (unsigned long long)h2);
}

/* Decode one UTF-8 codepoint, advancing *pos; returns UTF-16 code unit count (1 or 2) */
static size_t utf8_next_utf16_units(const char *s, size_t len, size_t *pos)
{
	uint8_t b = (uint8_t)s[*pos];
	size_t clen = 1;
	uint32_t cp = b;

	if ((b & 0x80) == 0) {
		clen = 1;
	} else if ((b & 0xe0) == 0xc0 && *pos + 1 < len) {
		clen = 2;
		cp = ((b & 0x1f) << 6) | ((uint8_t)s[*pos + 1] & 0x3f);
	} else if ((b & 0xf0) == 0xe0 && *pos + 2 < len) {
		clen = 3;
		cp = ((b & 0x0f) << 12) | (((uint8_t)s[*pos + 1] & 0x3f) << 6) |
		     ((uint8_t)s[*pos + 2] & 0x3f);
	} else if ((b & 0xf8) == 0xf0 && *pos + 3 < len) {
		clen = 4;
		cp = ((b & 0x07) << 18) | (((uint8_t)s[*pos + 1] & 0x3f) << 12) |
		     (((uint8_t)s[*pos + 2] & 0x3f) << 6) | ((uint8_t)s[*pos + 3] & 0x3f);
	}
	*pos += clen;
	return cp > 0xffff ? 2 : 1;
}

size_t utf16_len(const char *utf8, size_t byte_len)
{
	size_t pos = 0;
	size_t units = 0;

	while (pos < byte_len)
		units += utf8_next_utf16_units(utf8, byte_len, &pos);
	return units;
}

int split_message(const char *text, size_t max_utf16, char ***out_parts, size_t *out_count)
{
	size_t cap = 4;
	size_t count = 0;
	char **parts;
	const char *p;
	size_t rem;

	if (max_utf16 < 2)
		return -1;

	parts = calloc(cap, sizeof(char *));
	if (!parts)
		return -1;

	if (!text || !*text) {
		parts[0] = xstrdup("");
		*out_parts = parts;
		*out_count = 1;
		return 0;
	}

	p = text;
	rem = strlen(p);
	while (rem > 0) {
		size_t pos = 0;
		size_t units = 0;
		size_t split_at;
		const char *nl;

		while (pos < rem) {
			size_t next = pos;
			size_t w = utf8_next_utf16_units(p, rem, &next);
			if (units + w > max_utf16)
				break;
			units += w;
			pos = next;
		}

		if (pos == rem) {
			split_at = rem;
		} else {
			nl = NULL;
			for (size_t i = pos; i > 0; i--) {
				if (p[i - 1] == '\n') {
					nl = p + i - 1;
					break;
				}
			}
			if (nl && nl > p)
				split_at = (size_t)(nl - p);
			else
				split_at = pos > 0 ? pos : 1;
		}

		if (count == cap) {
			char **np;
			cap *= 2;
			np = realloc(parts, cap * sizeof(char *));
			if (!np) {
				free_str_array(parts, count);
				return -1;
			}
			parts = np;
		}

		parts[count] = malloc(split_at + 1);
		if (!parts[count]) {
			free_str_array(parts, count);
			return -1;
		}
		memcpy(parts[count], p, split_at);
		parts[count][split_at] = '\0';
		count++;

		if (pos == rem) {
			break;
		} else if (split_at < rem && p[split_at] == '\n') {
			p += split_at + 1;
			rem -= split_at + 1;
		} else {
			p += split_at;
			rem -= split_at;
		}
	}

	*out_parts = parts;
	*out_count = count;
	return 0;
}

void free_str_array(char **arr, size_t count)
{
	if (!arr)
		return;
	for (size_t i = 0; i < count; i++)
		free(arr[i]);
	free(arr);
}

bool path_is_dir(const char *path)
{
	struct stat st;
	return path && stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

bool path_is_file(const char *path)
{
	struct stat st;
	return path && stat(path, &st) == 0 && S_ISREG(st.st_mode);
}

bool path_is_symlink(const char *path)
{
	struct stat st;
	return path && lstat(path, &st) == 0 && S_ISLNK(st.st_mode);
}

bool mkdir_p(const char *path, int mode)
{
	char tmp[512];
	char *p;

	if (!path || !*path)
		return false;
	snprintf(tmp, sizeof(tmp), "%s", path);
	for (p = tmp + 1; *p; p++) {
		if (*p == '/') {
			*p = '\0';
			if (mkdir(tmp, (mode_t)mode) != 0 && errno != EEXIST)
				return false;
			*p = '/';
		}
	}
	return mkdir(tmp, (mode_t)mode) == 0 || errno == EEXIST;
}
