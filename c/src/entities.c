#include "ccgram.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
 * Lightweight Markdown -> Telegram MessageEntity converter.
 * Handles:
 *   - 4-space indented paragraph stripping outside fenced code blocks
 *   - ```lang\n...\n``` fenced code blocks -> "pre" entity
 *   - `inline code` -> "code" entity
 *   - **bold** and *bold* -> "bold" entity
 *   - _italic_ -> "italic" entity
 *   - [label](url) -> "text_link" entity
 */

static char *strip_indented_code_blocks(const char *text)
{
	struct strbuf out;
	const char *p = text;
	bool inside_fence = false;
	char fence_ch = 0;
	size_t fence_len = 0;
	bool prev_blank = true;

	sb_init(&out);
	while (*p) {
		const char *nl = strchr(p, '\n');
		size_t llen = nl ? (size_t)(nl - p) : strlen(p);
		bool is_fence = false;

		if (llen >= 3 && (p[0] == '`' || p[0] == '~')) {
			char fc = p[0];
			size_t fl = 0;
			while (fl < llen && p[fl] == fc)
				fl++;
			if (fl >= 3) {
				is_fence = true;
				if (!inside_fence) {
					inside_fence = true;
					fence_ch = fc;
					fence_len = fl;
				} else if (fc == fence_ch && fl >= fence_len) {
					inside_fence = false;
				}
			}
		}

		if (!inside_fence && !is_fence && prev_blank && llen >= 4 &&
		    strncmp(p, "    ", 4) == 0) {
			sb_append(&out, p + 4, llen - 4);
		} else {
			sb_append(&out, p, llen);
		}

		if (nl) {
			sb_append(&out, "\n", 1);
			prev_blank = (llen == 0);
			p = nl + 1;
		} else {
			break;
		}
	}
	return sb_detach(&out);
}

static bool add_entity(struct tg_entity **ents, size_t *count, size_t *cap,
		       const char *type, int offset, int length,
		       const char *url, const char *lang)
{
	struct tg_entity *e;

	if (length <= 0)
		return true;
	if (*count == *cap) {
		size_t ncap = *cap ? *cap * 2 : 16;
		struct tg_entity *ne = realloc(*ents, ncap * sizeof(struct tg_entity));
		if (!ne)
			return false;
		*ents = ne;
		*cap = ncap;
	}
	e = &(*ents)[(*count)++];
	memset(e, 0, sizeof(*e));
	snprintf(e->type, sizeof(e->type), "%s", type);
	e->offset = offset;
	e->length = length;
	if (url)
		snprintf(e->url, sizeof(e->url), "%s", url);
	if (lang)
		snprintf(e->language, sizeof(e->language), "%s", lang);
	return true;
}

void convert_to_entities(const char *markdown, char **out_plain,
			 struct tg_entity **out_ents, size_t *out_count)
{
	char *pre = strip_indented_code_blocks(markdown ? markdown : "");
	struct strbuf plain;
	struct tg_entity *ents = NULL;
	size_t ent_count = 0, ent_cap = 0;
	const char *p = pre;
	int u16_pos = 0;

	sb_init(&plain);

	while (*p) {
		/* Fenced code block at start of line */
		if ((p == pre || p[-1] == '\n') && strncmp(p, "```", 3) == 0) {
			const char *lang_start = p + 3;
			const char *eol = strchr(lang_start, '\n');
			if (eol) {
				char lang[64] = {0};
				size_t llen = (size_t)(eol - lang_start);
				while (llen > 0 && isspace((unsigned char)lang_start[llen - 1]))
					llen--;
				if (llen >= sizeof(lang))
					llen = sizeof(lang) - 1;
				memcpy(lang, lang_start, llen);
				lang[llen] = '\0';

				const char *body = eol + 1;
				const char *close = strstr(body, "\n```");
				if (!close && strncmp(body, "```", 3) == 0)
					close = body;
				if (close) {
					size_t blen = (size_t)(close - body);
					int start_u16 = u16_pos;
					int len_u16 = (int)utf16_len(body, blen);
					sb_append(&plain, body, blen);
					u16_pos += len_u16;
					add_entity(&ents, &ent_count, &ent_cap, "pre",
						   start_u16, len_u16, NULL, lang[0] ? lang : NULL);
					p = (close == body) ? close + 3 : close + 4;
					if (*p == '\n' && p[1] != '\0') {
						sb_append(&plain, "\n", 1);
						u16_pos += 1;
						p++;
					}
					continue;
				}
			}
		}

		/* Inline code `...` */
		if (*p == '`') {
			const char *close = strchr(p + 1, '`');
			if (close && close > p + 1) {
				const char *nl = memchr(p + 1, '\n', (size_t)(close - (p + 1)));
				if (!nl) {
					size_t clen = (size_t)(close - (p + 1));
					int len_u16 = (int)utf16_len(p + 1, clen);
					add_entity(&ents, &ent_count, &ent_cap, "code",
						   u16_pos, len_u16, NULL, NULL);
					sb_append(&plain, p + 1, clen);
					u16_pos += len_u16;
					p = close + 1;
					continue;
				}
			}
		}

		/* Link [label](url) */
		if (*p == '[') {
			const char *rb = strchr(p + 1, ']');
			if (rb && rb[1] == '(') {
				const char *rp = strchr(rb + 2, ')');
				if (rp && rb > p + 1 && rp > rb + 2) {
					size_t llen = (size_t)(rb - (p + 1));
					size_t ulen = (size_t)(rp - (rb + 2));
					if (ulen < 512) {
						char url[512];
						int len_u16 = (int)utf16_len(p + 1, llen);
						memcpy(url, rb + 2, ulen);
						url[ulen] = '\0';
						add_entity(&ents, &ent_count, &ent_cap, "text_link",
							   u16_pos, len_u16, url, NULL);
						sb_append(&plain, p + 1, llen);
						u16_pos += len_u16;
						p = rp + 1;
						continue;
					}
				}
			}
		}

		/* **bold** or *bold* */
		if (*p == '*') {
			bool dbl = (p[1] == '*');
			const char *start = dbl ? p + 2 : p + 1;
			const char *close = dbl ? strstr(start, "**") : strchr(start, '*');
			if (close && close > start) {
				const char *nl = memchr(start, '\n', (size_t)(close - start));
				if (!nl) {
					size_t blen = (size_t)(close - start);
					int len_u16 = (int)utf16_len(start, blen);
					add_entity(&ents, &ent_count, &ent_cap, "bold",
						   u16_pos, len_u16, NULL, NULL);
					sb_append(&plain, start, blen);
					u16_pos += len_u16;
					p = close + (dbl ? 2 : 1);
					continue;
				}
			}
		}

		/* _italic_ (only when bounded by non-alnum) */
		if (*p == '_' && (p == pre || !isalnum((unsigned char)p[-1]))) {
			const char *close = strchr(p + 1, '_');
			if (close && close > p + 1 && !isalnum((unsigned char)close[1])) {
				const char *nl = memchr(p + 1, '\n', (size_t)(close - (p + 1)));
				if (!nl) {
					size_t ilen = (size_t)(close - (p + 1));
					int len_u16 = (int)utf16_len(p + 1, ilen);
					add_entity(&ents, &ent_count, &ent_cap, "italic",
						   u16_pos, len_u16, NULL, NULL);
					sb_append(&plain, p + 1, ilen);
					u16_pos += len_u16;
					p = close + 1;
					continue;
				}
			}
		}

		/* Normal UTF-8 character */
		size_t char_bytes = 1;
		uint8_t b = (uint8_t)*p;
		if ((b & 0xe0) == 0xc0 && p[1])
			char_bytes = 2;
		else if ((b & 0xf0) == 0xe0 && p[1] && p[2])
			char_bytes = 3;
		else if ((b & 0xf8) == 0xf0 && p[1] && p[2] && p[3])
			char_bytes = 4;

		sb_append(&plain, p, char_bytes);
		u16_pos += (int)utf16_len(p, char_bytes);
		p += char_bytes;
	}

	free(pre);
	*out_plain = sb_detach(&plain);
	*out_ents = ents;
	*out_count = ent_count;
}
