import struct, zlib, sys
def inflate_head(path):
    b = open(path, 'rb').read()
    nl, el = struct.unpack('<HH', b[26:30])
    return zlib.decompressobj(-15).decompress(b[30 + nl + el:]).decode('utf-8', 'replace')
def rows(path):
    for l in inflate_head(path).split('\n'):
        p = l.split('\t')
        if len(p) >= 7 and len(p[0]) == 40:
            yield p
