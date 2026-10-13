"""Read Inquisitor DRPK archives; never writes the input archive.

Format references: https://aluigi.altervista.org/bms/inquisitor.bms
https://github.com/LittleBigBug/QuickBMS/blob/master/src/unz.c
https://imatix-legacy.github.io/sfl/sfldoc.htm (expand_block)
Python implementation of the documented SFL block format.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path


def expand_block(src):
    if src[0] == 0x80:
        return src[1:]
    assert src[0] == 0x40, 'Unknown SFL flag'
    out = bytearray()
    pos = 1
    bits = command = 0
    while pos < len(src):
        if not bits:
            command = int.from_bytes(src[pos:pos+2], 'big')
            pos += 2
            bits = 16
        if command & 0x8000:
            distance = (src[pos] << 4) | (src[pos+1] >> 4)
            pos += 1
            if distance:
                length = (src[pos] & 15) + 3
                pos += 1
                assert distance <= len(out)
                for _ in range(length):
                    out.append(out[-distance])
            else:
                length = (src[pos] << 8) + src[pos+1] + 16
                pos += 2
                out.extend(bytes([src[pos]]) * length)
                pos += 1
        else:
            out.append(src[pos])
            pos += 1
        command <<= 1
        bits -= 1
    assert pos == len(src)
    return bytes(out)


def expand(src, expected):
    out = bytearray()
    pos = 0
    while pos < len(src):
        size = struct.unpack_from('<H', src, pos)[0]
        pos += 2
        if not size:
            break
        assert pos + size <= len(src)
        out.extend(expand_block(src[pos:pos+size]))
        pos += size
    assert len(out) == expected, (len(out), expected)
    assert pos == len(src), 'Trailing compressed bytes'
    return bytes(out)


def entries(path):
    original = path.read_bytes()
    assert original[:4] == b'DRPK'
    data = original.translate(bytes(x ^ 0x4a for x in range(256)))
    count, dirsize, namesize, _ = struct.unpack_from('<4I', data, len(data)-16)
    names = len(data)-16-namesize
    dirs = names-dirsize
    info = dirs-count*24
    assert info >= 6
    def name_at(offset):
        return data[offset:data.index(0, offset)].decode('cp1252')
    for i in range(count):
        d, n, size, packed, offset, _ = struct.unpack_from('<6I', data, info+i*24)
        name = (name_at(dirs+d)+'/'+name_at(names+n)).replace('\\', '/')
        assert offset >= 6 and offset+packed <= info
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        yield name, size, packed, offset, data[offset:offset+packed]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, size, packed, offset, compressed in entries(args.archive):
        row = dict(path=name, size=size, packed_size=packed, offset=offset)
        if Path(name).suffix.lower() in {'.txt', '.dlg', '.dlt', '.cht', '.itm', '.bfi'}:
            content = expand(compressed, size)
            target = args.output/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            row['sha256'] = hashlib.sha256(content).hexdigest()
        manifest.append(row)
    (args.output/'archive_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(args.archive.name, 'entries', len(manifest), 'extracted', sum('sha256' in r for r in manifest))


if __name__ == '__main__':
    main()
