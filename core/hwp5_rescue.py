#!/usr/bin/env python3
"""Extract body text from HWP 5.x files that pyhwp refuses to open.

Some producers (e.g. Clova Note exports) omit the \\x05HwpSummaryInformation
OLE stream, which pyhwp treats as fatal. The document body is intact, so this
reads BodyText/Section* directly and decodes the HWPTAG_PARA_TEXT records.

Must run under the pyhwp virtualenv python (it provides `olefile`):
  ~/.local/share/uv/tools/pyhwp/bin/python hwp5_rescue.py <file.hwp>
"""
import struct
import sys
import zlib

import olefile

HWPTAG_PARA_TEXT = 0x10 + 51

# Control chars that carry an 8-wchar (16-byte) inline payload.
EXTENDED_CTRL = {1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}
# Control chars that map to a line/paragraph break.
BREAK_CTRL = {10, 13}


def records(buf):
    pos, size = 0, len(buf)
    while pos + 4 <= size:
        (header,) = struct.unpack_from("<I", buf, pos)
        pos += 4
        tag = header & 0x3FF
        length = (header >> 20) & 0xFFF
        if length == 0xFFF:
            (length,) = struct.unpack_from("<I", buf, pos)
            pos += 4
        yield tag, buf[pos:pos + length]
        pos += length


def decode_para_text(data):
    out, i, n = [], 0, len(data)
    while i + 2 <= n:
        (code,) = struct.unpack_from("<H", data, i)
        if code in EXTENDED_CTRL:
            i += 16
        elif code in BREAK_CTRL:
            out.append("\n")
            i += 2
        elif code < 32:
            i += 2
        else:
            out.append(chr(code))
            i += 2
    return "".join(out)


def extract(path):
    ole = olefile.OleFileIO(path)
    header = ole.openstream("FileHeader").read(40)
    compressed = bool(struct.unpack_from("<I", header, 36)[0] & 1)

    sections = sorted(
        ("/".join(s) for s in ole.listdir() if s[0] == "BodyText"),
        key=lambda p: int("".join(c for c in p.rsplit("/", 1)[-1] if c.isdigit()) or 0),
    )
    paragraphs = []
    for name in sections:
        raw = ole.openstream(name).read()
        if compressed:
            raw = zlib.decompress(raw, -15)
        for tag, data in records(raw):
            if tag == HWPTAG_PARA_TEXT:
                text = decode_para_text(data).strip()
                if text:
                    paragraphs.append(text)
    ole.close()
    return "\n\n".join(paragraphs)


if __name__ == "__main__":
    sys.stdout.write(extract(sys.argv[1]))
