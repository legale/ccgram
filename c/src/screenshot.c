#include "ccgram.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>

struct rgb {
	uint8_t r, g, b;
};

static const struct rgb ANSI_COLORS[16] = {
	{ 0, 0, 0 },       /* 0 Black */
	{ 205, 49, 49 },   /* 1 Red */
	{ 13, 188, 121 },  /* 2 Green */
	{ 229, 229, 16 },  /* 3 Yellow */
	{ 36, 114, 200 },  /* 4 Blue */
	{ 188, 63, 188 },  /* 5 Magenta */
	{ 17, 168, 205 },  /* 6 Cyan */
	{ 229, 229, 229 }, /* 7 White */
	{ 102, 102, 102 }, /* 8 Bright Black */
	{ 241, 76, 76 },   /* 9 Bright Red */
	{ 35, 209, 139 },  /* 10 Bright Green */
	{ 245, 245, 67 },  /* 11 Bright Yellow */
	{ 59, 142, 234 },  /* 12 Bright Blue */
	{ 214, 112, 214 }, /* 13 Bright Magenta */
	{ 41, 184, 219 },  /* 14 Bright Cyan */
	{ 255, 255, 255 }, /* 15 Bright White */
};

static const struct rgb DEFAULT_FG = { 212, 212, 212 };
static const struct rgb DEFAULT_BG = { 30, 30, 30 };

static struct rgb approx_256(int idx)
{
	if (idx < 0)
		idx = 0;
	idx %= 256;
	if (idx < 16)
		return ANSI_COLORS[idx];
	if (idx < 232) {
		int c = idx - 16;
		return (struct rgb){
			(uint8_t)((c / 36) * 51),
			(uint8_t)(((c % 36) / 6) * 51),
			(uint8_t)((c % 6) * 51),
		};
	}
	uint8_t g = (uint8_t)(8 + (idx - 232) * 10);
	return (struct rgb){ g, g, g };
}

/* Compact 5x7 pixel font for ASCII 0x20..0x7E (each char = 5 columns, 7 rows LSB=top) */
static const uint8_t FONT5X7[95][5] = {
	{0x00,0x00,0x00,0x00,0x00}, /*   */
	{0x00,0x00,0x5f,0x00,0x00}, /* ! */
	{0x00,0x07,0x00,0x07,0x00}, /* " */
	{0x14,0x7f,0x14,0x7f,0x14}, /* # */
	{0x24,0x2a,0x7f,0x2a,0x12}, /* $ */
	{0x23,0x13,0x08,0x64,0x62}, /* % */
	{0x36,0x49,0x55,0x22,0x50}, /* & */
	{0x00,0x05,0x03,0x00,0x00}, /* ' */
	{0x00,0x1c,0x22,0x41,0x00}, /* ( */
	{0x00,0x41,0x22,0x1c,0x00}, /* ) */
	{0x14,0x08,0x3e,0x08,0x14}, /* * */
	{0x08,0x08,0x3e,0x08,0x08}, /* + */
	{0x00,0x50,0x30,0x00,0x00}, /* , */
	{0x08,0x08,0x08,0x08,0x08}, /* - */
	{0x00,0x60,0x60,0x00,0x00}, /* . */
	{0x20,0x10,0x08,0x04,0x02}, /* / */
	{0x3e,0x51,0x49,0x45,0x3e}, /* 0 */
	{0x00,0x42,0x7f,0x40,0x00}, /* 1 */
	{0x42,0x61,0x51,0x49,0x46}, /* 2 */
	{0x21,0x41,0x45,0x4b,0x31}, /* 3 */
	{0x18,0x14,0x12,0x7f,0x10}, /* 4 */
	{0x27,0x45,0x45,0x45,0x39}, /* 5 */
	{0x3c,0x4a,0x49,0x49,0x30}, /* 6 */
	{0x01,0x71,0x09,0x05,0x03}, /* 7 */
	{0x36,0x49,0x49,0x49,0x36}, /* 8 */
	{0x06,0x49,0x49,0x29,0x1e}, /* 9 */
	{0x00,0x36,0x36,0x00,0x00}, /* : */
	{0x00,0x56,0x36,0x00,0x00}, /* ; */
	{0x08,0x14,0x22,0x41,0x00}, /* < */
	{0x14,0x14,0x14,0x14,0x14}, /* = */
	{0x00,0x41,0x22,0x14,0x08}, /* > */
	{0x02,0x01,0x51,0x09,0x06}, /* ? */
	{0x32,0x49,0x79,0x41,0x3e}, /* @ */
	{0x7e,0x11,0x11,0x11,0x7e}, /* A */
	{0x7f,0x49,0x49,0x49,0x36}, /* B */
	{0x3e,0x41,0x41,0x41,0x22}, /* C */
	{0x7f,0x41,0x41,0x22,0x1c}, /* D */
	{0x7f,0x49,0x49,0x49,0x41}, /* E */
	{0x7f,0x09,0x09,0x09,0x01}, /* F */
	{0x3e,0x41,0x49,0x49,0x7a}, /* G */
	{0x7f,0x08,0x08,0x08,0x7f}, /* H */
	{0x00,0x41,0x7f,0x41,0x00}, /* I */
	{0x20,0x40,0x41,0x3f,0x01}, /* J */
	{0x7f,0x08,0x14,0x22,0x41}, /* K */
	{0x7f,0x40,0x40,0x40,0x40}, /* L */
	{0x7f,0x02,0x0c,0x02,0x7f}, /* M */
	{0x7f,0x04,0x08,0x10,0x7f}, /* N */
	{0x3e,0x41,0x41,0x41,0x3e}, /* O */
	{0x7f,0x09,0x09,0x09,0x06}, /* P */
	{0x3e,0x41,0x51,0x21,0x5e}, /* Q */
	{0x7f,0x09,0x19,0x29,0x46}, /* R */
	{0x46,0x49,0x49,0x49,0x31}, /* S */
	{0x01,0x01,0x7f,0x01,0x01}, /* T */
	{0x3f,0x40,0x40,0x40,0x3f}, /* U */
	{0x1f,0x20,0x40,0x20,0x1f}, /* V */
	{0x3f,0x40,0x38,0x40,0x3f}, /* W */
	{0x63,0x14,0x08,0x14,0x63}, /* X */
	{0x07,0x08,0x70,0x08,0x07}, /* Y */
	{0x61,0x51,0x49,0x45,0x43}, /* Z */
	{0x00,0x7f,0x41,0x41,0x00}, /* [ */
	{0x02,0x04,0x08,0x10,0x20}, /* \ */
	{0x00,0x41,0x41,0x7f,0x00}, /* ] */
	{0x04,0x02,0x01,0x02,0x04}, /* ^ */
	{0x40,0x40,0x40,0x40,0x40}, /* _ */
	{0x00,0x01,0x02,0x04,0x00}, /* ` */
	{0x20,0x54,0x54,0x54,0x78}, /* a */
	{0x7f,0x48,0x44,0x44,0x38}, /* b */
	{0x38,0x44,0x44,0x44,0x20}, /* c */
	{0x38,0x44,0x44,0x48,0x7f}, /* d */
	{0x38,0x54,0x54,0x54,0x18}, /* e */
	{0x08,0x7e,0x09,0x01,0x02}, /* f */
	{0x0c,0x52,0x52,0x52,0x3e}, /* g */
	{0x7f,0x08,0x04,0x04,0x78}, /* h */
	{0x00,0x44,0x7d,0x40,0x00}, /* i */
	{0x20,0x40,0x44,0x3d,0x00}, /* j */
	{0x7f,0x10,0x28,0x44,0x00}, /* k */
	{0x00,0x41,0x7f,0x40,0x00}, /* l */
	{0x7c,0x04,0x18,0x04,0x78}, /* m */
	{0x7c,0x08,0x04,0x04,0x78}, /* n */
	{0x38,0x44,0x44,0x44,0x38}, /* o */
	{0x7c,0x14,0x14,0x14,0x08}, /* p */
	{0x08,0x14,0x14,0x18,0x7c}, /* q */
	{0x7c,0x08,0x04,0x04,0x08}, /* r */
	{0x48,0x54,0x54,0x54,0x20}, /* s */
	{0x04,0x3f,0x44,0x40,0x20}, /* t */
	{0x3c,0x40,0x40,0x20,0x7c}, /* u */
	{0x1c,0x20,0x40,0x20,0x1c}, /* v */
	{0x3c,0x40,0x30,0x40,0x3c}, /* w */
	{0x44,0x28,0x10,0x28,0x44}, /* x */
	{0x0c,0x50,0x50,0x50,0x3c}, /* y */
	{0x44,0x64,0x54,0x4c,0x44}, /* z */
	{0x00,0x08,0x36,0x41,0x00}, /* { */
	{0x00,0x00,0x7f,0x00,0x00}, /* | */
	{0x00,0x41,0x36,0x08,0x00}, /* } */
	{0x10,0x08,0x08,0x10,0x08}, /* ~ */
};

struct cell {
	uint32_t cp;
	struct rgb fg;
	struct rgb bg;
	bool has_bg;
};

static uint32_t decode_utf8(const char **p)
{
	const uint8_t *s = (const uint8_t *)*p;
	uint32_t cp;

	if ((s[0] & 0x80) == 0) {
		*p += 1;
		return s[0];
	} else if ((s[0] & 0xe0) == 0xc0 && s[1]) {
		cp = ((s[0] & 0x1f) << 6) | (s[1] & 0x3f);
		*p += 2;
		return cp;
	} else if ((s[0] & 0xf0) == 0xe0 && s[1] && s[2]) {
		cp = ((s[0] & 0x0f) << 12) | ((s[1] & 0x3f) << 6) | (s[2] & 0x3f);
		*p += 3;
		return cp;
	} else if ((s[0] & 0xf8) == 0xf0 && s[1] && s[2] && s[3]) {
		cp = ((s[0] & 0x07) << 18) | ((s[1] & 0x3f) << 12) |
		     ((s[2] & 0x3f) << 6) | (s[3] & 0x3f);
		*p += 4;
		return cp;
	}
	*p += 1;
	return '?';
}

static void apply_sgr(const char *codes, size_t len, struct rgb *fg, struct rgb *bg, bool *has_bg)
{
	int parts[32];
	int n = 0;
	int val = 0;
	bool in_num = false;

	if (len == 0) {
		*fg = DEFAULT_FG;
		*has_bg = false;
		return;
	}

	for (size_t i = 0; i <= len && n < 32; i++) {
		if (i < len && isdigit((unsigned char)codes[i])) {
			val = val * 10 + (codes[i] - '0');
			in_num = true;
		} else if (i == len || codes[i] == ';') {
			parts[n++] = in_num ? val : 0;
			val = 0;
			in_num = false;
		}
	}

	for (int i = 0; i < n; i++) {
		int c = parts[i];
		if (c == 0) {
			*fg = DEFAULT_FG;
			*has_bg = false;
		} else if (c >= 30 && c <= 37) {
			*fg = ANSI_COLORS[c - 30];
		} else if (c == 38) {
			if (i + 2 < n && parts[i + 1] == 5) {
				*fg = approx_256(parts[i + 2]);
				i += 2;
			} else if (i + 4 < n && parts[i + 1] == 2) {
				*fg = (struct rgb){ (uint8_t)parts[i + 2], (uint8_t)parts[i + 3], (uint8_t)parts[i + 4] };
				i += 4;
			}
		} else if (c == 39) {
			*fg = DEFAULT_FG;
		} else if (c >= 40 && c <= 47) {
			*bg = ANSI_COLORS[c - 40];
			*has_bg = true;
		} else if (c == 48) {
			if (i + 2 < n && parts[i + 1] == 5) {
				*bg = approx_256(parts[i + 2]);
				*has_bg = true;
				i += 2;
			} else if (i + 4 < n && parts[i + 1] == 2) {
				*bg = (struct rgb){ (uint8_t)parts[i + 2], (uint8_t)parts[i + 3], (uint8_t)parts[i + 4] };
				*has_bg = true;
				i += 4;
			}
		} else if (c == 49) {
			*has_bg = false;
		} else if (c >= 90 && c <= 97) {
			*fg = ANSI_COLORS[c - 90 + 8];
		} else if (c >= 100 && c <= 107) {
			*bg = ANSI_COLORS[c - 100 + 8];
			*has_bg = true;
		}
	}
}

static bool glyph_pixel(uint32_t cp, int col, int row)
{
	if (cp >= 0x20 && cp <= 0x7e) {
		if (col >= 0 && col < 5 && row >= 0 && row < 7)
			return (FONT5X7[cp - 0x20][col] >> row) & 1;
		return false;
	}
	/* Box-drawing & common terminal symbols */
	if (cp == 0x2500 || cp == 0x2501) return row == 3; /* ─ */
	if (cp == 0x2502 || cp == 0x2503) return col == 2; /* │ */
	if (cp == 0x250c) return (row == 3 && col >= 2) || (col == 2 && row >= 3); /* ┌ */
	if (cp == 0x2510) return (row == 3 && col <= 2) || (col == 2 && row >= 3); /* ┐ */
	if (cp == 0x2514) return (row == 3 && col >= 2) || (col == 2 && row <= 3); /* └ */
	if (cp == 0x2518) return (row == 3 && col <= 2) || (col == 2 && row <= 3); /* ┘ */
	if (cp == 0x251c) return col == 2 || (row == 3 && col >= 2); /* ├ */
	if (cp == 0x2524) return col == 2 || (row == 3 && col <= 2); /* ┤ */
	if (cp == 0x252c) return row == 3 || (col == 2 && row >= 3); /* ┬ */
	if (cp == 0x2534) return row == 3 || (col == 2 && row <= 3); /* ┴ */
	if (cp == 0x253c) return row == 3 || col == 2; /* ┼ */
	if (cp == 0x2588) return true; /* █ */
	if (cp == 0x2022 || cp == 0x25cf) return (col >= 1 && col <= 3 && row >= 2 && row <= 4); /* • ● */
	if (cp == 0x2191) return (col == 2 && row >= 1 && row <= 5) || (row == 2 && (col == 1 || col == 3)); /* ↑ */
	if (cp == 0x2193) return (col == 2 && row >= 1 && row <= 5) || (row == 4 && (col == 1 || col == 3)); /* ↓ */
	if (cp == 0x2190) return (row == 3 && col >= 0 && col <= 4) || (col == 1 && (row == 2 || row == 4)); /* ← */
	if (cp == 0x2192) return (row == 3 && col >= 0 && col <= 4) || (col == 3 && (row == 2 || row == 4)); /* → */
	if (cp == 0x2026) return row == 6 && (col == 0 || col == 2 || col == 4); /* … */
	/* Fallback box for non-ASCII */
	return (col == 0 || col == 4 || row == 0 || row == 6);
}

static void write_be32(uint8_t *p, uint32_t v)
{
	p[0] = (uint8_t)((v >> 24) & 0xff);
	p[1] = (uint8_t)((v >> 16) & 0xff);
	p[2] = (uint8_t)((v >> 8) & 0xff);
	p[3] = (uint8_t)(v & 0xff);
}

static void append_png_chunk(struct strbuf *sb, const char *type, const uint8_t *data, uint32_t len)
{
	uint8_t len_be[4];
	uint8_t crc_be[4];
	uLong crc;

	write_be32(len_be, len);
	sb_append(sb, (const char *)len_be, 4);
	sb_append(sb, type, 4);
	if (len > 0)
		sb_append(sb, (const char *)data, len);

	crc = crc32(0L, Z_NULL, 0);
	crc = crc32(crc, (const Bytef *)type, 4);
	if (len > 0)
		crc = crc32(crc, (const Bytef *)data, len);
	write_be32(crc_be, (uint32_t)crc);
	sb_append(sb, (const char *)crc_be, 4);
}

uint8_t *screenshot_text_to_png(const char *text, int font_size, bool with_ansi,
				 bool live_mode, size_t *out_len)
{
	int base_fs = font_size > 0 ? font_size : g_cfg.screenshot_font_size;
	int eff_fs = live_mode ? ((base_fs * 3) / 4) : base_fs;
	if (eff_fs < 10)
		eff_fs = 10;

	int scale = eff_fs >= 16 ? 2 : 1;
	int char_w = 6 * scale;
	int line_h = 10 * scale;
	int padding = 16;

	/* Count lines & max cols */
	size_t num_lines = 1;
	size_t max_cols = 0;
	for (const char *p = text ? text : ""; *p; p++) {
		if (*p == '\n')
			num_lines++;
	}

	struct cell **rows = calloc(num_lines, sizeof(struct cell *));
	size_t *row_lens = calloc(num_lines, sizeof(size_t));
	if (!rows || !row_lens) {
		free(rows);
		free(row_lens);
		return NULL;
	}

	/* Parse lines */
	const char *cur = text ? text : "";
	for (size_t r = 0; r < num_lines; r++) {
		const char *nl = strchr(cur, '\n');
		size_t blen = nl ? (size_t)(nl - cur) : strlen(cur);
		struct cell *cells = calloc(blen * 4 + 1, sizeof(struct cell));
		size_t col = 0;
		struct rgb fg = DEFAULT_FG;
		struct rgb bg = DEFAULT_BG;
		bool has_bg = false;
		const char *p = cur;
		const char *end = cur + blen;

		if (!cells)
			break;

		while (p < end) {
			if (with_ansi && p[0] == '\x1b' && p + 1 < end && p[1] == '[') {
				const char *q = p + 2;
				while (q < end && ((*q >= '0' && *q <= '9') || *q == ';'))
					q++;
				if (q < end && *q == 'm') {
					apply_sgr(p + 2, (size_t)(q - (p + 2)), &fg, &bg, &has_bg);
					p = q + 1;
					continue;
				}
			}
			uint32_t cp = decode_utf8(&p);
			if (cp == '\r')
				continue;
			if (cp == '\t') {
				for (int t = 0; t < 4; t++) {
					cells[col++] = (struct cell){ ' ', fg, bg, has_bg };
				}
				continue;
			}
			cells[col++] = (struct cell){ cp, fg, bg, has_bg };
		}
		rows[r] = cells;
		row_lens[r] = col;
		if (col > max_cols)
			max_cols = col;

		if (!nl)
			break;
		cur = nl + 1;
	}

	int img_w = (int)max_cols * char_w + padding * 2;
	int img_h = (int)num_lines * line_h + padding * 2;
	if (img_w < 200)
		img_w = 200;
	if (img_h < line_h + padding * 2)
		img_h = line_h + padding * 2;

	/* Raw scanlines: 1 filter byte + 3*img_w RGB bytes per row */
	size_t stride = 1 + (size_t)img_w * 3;
	size_t raw_len = stride * (size_t)img_h;
	uint8_t *raw = malloc(raw_len);
	if (!raw) {
		for (size_t r = 0; r < num_lines; r++)
			free(rows[r]);
		free(rows);
		free(row_lens);
		return NULL;
	}

	for (int y = 0; y < img_h; y++) {
		uint8_t *row_ptr = raw + (size_t)y * stride;
		row_ptr[0] = 0; /* filter type 0 */
		for (int x = 0; x < img_w; x++) {
			row_ptr[1 + x * 3 + 0] = DEFAULT_BG.r;
			row_ptr[1 + x * 3 + 1] = DEFAULT_BG.g;
			row_ptr[1 + x * 3 + 2] = DEFAULT_BG.b;
		}
	}

	/* Render text cells */
	for (size_t r = 0; r < num_lines; r++) {
		int y0 = padding + (int)r * line_h;
		for (size_t c = 0; c < row_lens[r]; c++) {
			int x0 = padding + (int)c * char_w;
			struct cell *cl = &rows[r][c];
			if (cl->has_bg) {
				for (int dy = 0; dy < line_h && y0 + dy < img_h; dy++) {
					uint8_t *rp = raw + (size_t)(y0 + dy) * stride;
					for (int dx = 0; dx < char_w && x0 + dx < img_w; dx++) {
						rp[1 + (x0 + dx) * 3 + 0] = cl->bg.r;
						rp[1 + (x0 + dx) * 3 + 1] = cl->bg.g;
						rp[1 + (x0 + dx) * 3 + 2] = cl->bg.b;
					}
				}
			}
			if (cl->cp == ' ')
				continue;
			for (int gr = 0; gr < 7; gr++) {
				for (int gc = 0; gc < 5; gc++) {
					if (!glyph_pixel(cl->cp, gc, gr))
						continue;
					for (int sy = 0; sy < scale; sy++) {
						int py = y0 + (gr + 1) * scale + sy;
						if (py < 0 || py >= img_h)
							continue;
						uint8_t *rp = raw + (size_t)py * stride;
						for (int sx = 0; sx < scale; sx++) {
							int px = x0 + gc * scale + sx;
							if (px < 0 || px >= img_w)
								continue;
							rp[1 + px * 3 + 0] = cl->fg.r;
							rp[1 + px * 3 + 1] = cl->fg.g;
							rp[1 + px * 3 + 2] = cl->fg.b;
						}
					}
				}
			}
		}
		free(rows[r]);
	}
	free(rows);
	free(row_lens);

	/* Compress with zlib */
	uLongf comp_cap = compressBound((uLong)raw_len);
	uint8_t *comp = malloc(comp_cap);
	if (!comp) {
		free(raw);
		return NULL;
	}
	int zlevel = live_mode ? 6 : 6;
	if (compress2(comp, &comp_cap, raw, (uLong)raw_len, zlevel) != Z_OK) {
		free(raw);
		free(comp);
		return NULL;
	}
	free(raw);

	/* Assemble PNG */
	struct strbuf png;
	sb_init(&png);
	static const uint8_t PNG_SIG[8] = { 137, 80, 78, 71, 13, 10, 26, 10 };
	sb_append(&png, (const char *)PNG_SIG, 8);

	uint8_t ihdr[13];
	write_be32(ihdr + 0, (uint32_t)img_w);
	write_be32(ihdr + 4, (uint32_t)img_h);
	ihdr[8] = 8; /* bit depth */
	ihdr[9] = 2; /* RGB */
	ihdr[10] = 0;
	ihdr[11] = 0;
	ihdr[12] = 0;
	append_png_chunk(&png, "IHDR", ihdr, 13);
	append_png_chunk(&png, "IDAT", comp, (uint32_t)comp_cap);
	append_png_chunk(&png, "IEND", NULL, 0);
	free(comp);

	*out_len = png.len;
	return (uint8_t *)sb_detach(&png);
}
