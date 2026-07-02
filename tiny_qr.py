#!/usr/bin/env python3
"""Minimalny generator kodów QR (byte mode, EC poziom M, wersje 1-10) — zero zależności.

Wystarcza do challenge_url logowania Steam (~40-60 znaków). Używa go GUI
(dupedealer_gui.py) do narysowania kodu QR, który skanujesz w apce Steam.
Zwraca macierz 0/1 (listy wierszy); rysowanie to sprawa wołającego.

matrix = tiny_qr.encode("https://s.team/q/...")
"""

# --- GF(256), wielomiany Reeda-Solomona ---
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11d
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _rs_gen_poly(n):
    poly = [1]
    for i in range(n):
        poly = _poly_mul(poly, [1, _EXP[i]])
    return poly


def _poly_mul(a, b):
    res = [0] * (len(a) + len(b) - 1)
    for i, ca in enumerate(a):
        for j, cb in enumerate(b):
            if ca and cb:
                res[i + j] ^= _EXP[_LOG[ca] + _LOG[cb]]
    return res


def _rs_ecc(data, n_ecc):
    gen = _rs_gen_poly(n_ecc)
    rem = list(data) + [0] * n_ecc
    for i in range(len(data)):
        coef = rem[i]
        if coef:
            for j in range(1, len(gen)):
                rem[i + j] ^= _EXP[_LOG[gen[j]] + _LOG[coef]]
    return rem[len(data):]


# --- tabele dla EC poziomu M, wersje 1-10 ---
# wersja -> (ecc na blok, [(liczba bloków, data-codewords na blok), ...])
_BLOCKS_M = {
    1: (10, [(1, 16)]),
    2: (16, [(1, 28)]),
    3: (26, [(1, 44)]),
    4: (18, [(2, 32)]),
    5: (24, [(2, 43)]),
    6: (16, [(4, 27)]),
    7: (18, [(4, 31)]),
    8: (22, [(2, 38), (2, 39)]),
    9: (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}
# środki wzorców pozycjonujących (alignment), wersje 2-10
_ALIGN = {2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
          7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}


def _data_capacity(version):
    ecc, groups = _BLOCKS_M[version]
    return sum(n * dc for n, dc in groups)


def _bits_to_codewords(payload, version):
    """Nagłówek byte-mode + dane + terminator + dopełnienie do pojemności wersji."""
    cap_bits = _data_capacity(version) * 8
    cci_bits = 8 if version <= 9 else 16
    bits = []

    def put(val, n):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    put(0b0100, 4)                 # mode: byte
    put(len(payload), cci_bits)    # character count
    for b in payload:
        put(b, 8)
    put(0, min(4, cap_bits - len(bits)))      # terminator
    while len(bits) % 8:
        bits.append(0)
    cw = [int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    i = 0
    while len(cw) < cap_bits // 8:
        cw.append(pad[i % 2]); i += 1
    return cw


def _interleave(cw, version):
    """Podział na bloki RS + przeplot danych i ECC wg specyfikacji."""
    n_ecc, groups = _BLOCKS_M[version]
    blocks, pos = [], 0
    for n, dc in groups:
        for _ in range(n):
            blocks.append(cw[pos:pos + dc]); pos += dc
    eccs = [_rs_ecc(b, n_ecc) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(n_ecc):
        for e in eccs:
            out.append(e[i])
    return out


# --- macierz ---
def _make_matrix(version):
    """Wzorce stałe. Zwraca (matrix, reserved) — reserved: moduły zajęte przez funkcje."""
    size = 17 + 4 * version
    m = [[0] * size for _ in range(size)]
    res = [[False] * size for _ in range(size)]

    def finder(r, c):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                rr, cc = r + dr, c + dc
                if 0 <= rr < size and 0 <= cc < size:
                    inside = 0 <= dr <= 6 and 0 <= dc <= 6
                    ring = inside and (dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4))
                    m[rr][cc] = 1 if ring else 0
                    res[rr][cc] = True

    finder(0, 0); finder(0, size - 7); finder(size - 7, 0)

    # alignment PRZED timingiem: środki na współrzędnej 6 leżą na linii timingu
    # i nie mogą zostać pominięte przez jej rezerwację
    for cy in _ALIGN.get(version, []):
        for cx in _ALIGN.get(version, []):
            if res[cy][cx]:
                continue                  # nachodzi na finder — pomiń
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    m[cy + dr][cx + dc] = 1 if max(abs(dr), abs(dc)) != 1 else 0
                    res[cy + dr][cx + dc] = True

    for i in range(8, size - 8):          # timing
        if not res[6][i]:
            m[6][i] = (i + 1) % 2; res[6][i] = True
        if not res[i][6]:
            m[i][6] = (i + 1) % 2; res[i][6] = True

    m[size - 8][8] = 1                    # dark module
    res[size - 8][8] = True

    # rezerwacja pól formatu (wartości wpisze _put_format)
    for i in range(9):
        if not res[8][i]:
            res[8][i] = True
        if not res[i][8]:
            res[i][8] = True
    for i in range(8):
        res[8][size - 1 - i] = True
        res[size - 1 - i][8] = True

    if version >= 7:                      # version info
        vi = version << 12
        rem = version << 12
        for i in range(17, 11, -1):
            if rem >> i:
                rem ^= 0x1f25 << (i - 12)
        vi |= rem
        for i in range(18):
            bit = (vi >> i) & 1
            m[size - 11 + i % 3][i // 3] = bit
            res[size - 11 + i % 3][i // 3] = True
            m[i // 3][size - 11 + i % 3] = bit
            res[i // 3][size - 11 + i % 3] = True
    return m, res


def _place_data(m, res, codewords):
    """Zygzak od prawego dolnego rogu, z pominięciem kolumny 6 (timing)."""
    size = len(m)
    bits = []
    for cw in codewords:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)
    idx, col, up = 0, size - 1, True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if up else range(size)
        for r in rows:
            for c in (col, col - 1):
                if not res[r][c]:
                    m[r][c] = bits[idx] if idx < len(bits) else 0
                    idx += 1
        col -= 2
        up = not up


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _put_format(m, mask):
    """15 bitów formatu: EC M ('00') + maska, BCH(15,5), XOR 0x5412."""
    size = len(m)
    data = (0b00 << 3) | mask
    rem = data << 10
    for i in range(14, 9, -1):
        if rem >> i:
            rem ^= 0x537 << (i - 10)
    fmt = ((data << 10) | rem) ^ 0x5412
    bits = [(fmt >> i) & 1 for i in range(14, -1, -1)]

    coords_a = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
                (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    coords_b = [(size - 1, 8), (size - 2, 8), (size - 3, 8), (size - 4, 8),
                (size - 5, 8), (size - 6, 8), (size - 7, 8),
                (8, size - 8), (8, size - 7), (8, size - 6), (8, size - 5),
                (8, size - 4), (8, size - 3), (8, size - 2), (8, size - 1)]
    for (r, c), b in zip(coords_a, bits):
        m[r][c] = b
    for (r, c), b in zip(coords_b, bits):
        m[r][c] = b


def _penalty(m):
    size = len(m)
    score = 0
    for rows in (m, list(zip(*m))):                       # N1: serie >=5
        for row in rows:
            run, prev = 0, None
            for v in list(row) + [None]:
                if v == prev:
                    run += 1
                else:
                    if prev is not None and run >= 5:
                        score += 3 + run - 5
                    run, prev = 1, v
    for r in range(size - 1):                             # N2: bloki 2x2
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    pat1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]              # N3: wzorce finder-podobne
    pat2 = pat1[::-1]
    for rows in (m, [list(col) for col in zip(*m)]):
        for row in rows:
            row = list(row)
            for i in range(size - 10):
                if row[i:i + 11] in (pat1, pat2):
                    score += 40
    dark = sum(sum(row) for row in m)                     # N4: proporcja ciemnych
    score += 10 * (abs(dark * 100 // (size * size) - 50) // 5)
    return score


def encode(text, mask=None):
    """Tekst/bajty -> macierz QR (lista wierszy, 0/1). EC poziom M, wersje 1-10.

    `mask` wymusza wzór maski 0-7 (do testów); domyślnie wybiera wg kary.
    """
    payload = text.encode('utf-8') if isinstance(text, str) else bytes(text)
    version = None
    for v in range(1, 11):
        cci = 8 if v <= 9 else 16
        if 4 + cci + 8 * len(payload) <= _data_capacity(v) * 8:
            version = v
            break
    if version is None:
        raise ValueError(f"za długie dane na QR v10-M ({len(payload)} bajtów)")

    codewords = _interleave(_bits_to_codewords(payload, version), version)

    best = None
    masks = [mask] if mask is not None else range(8)
    for mi in masks:
        m, res = _make_matrix(version)
        _place_data(m, res, codewords)
        for r in range(len(m)):
            for c in range(len(m)):
                if not res[r][c] and _MASKS[mi](r, c):
                    m[r][c] ^= 1
        _put_format(m, mi)
        p = _penalty(m)
        if best is None or p < best[0]:
            best = (p, m)
    return best[1]


if __name__ == '__main__':
    import sys
    mat = encode(sys.argv[1] if len(sys.argv) > 1 else "https://s.team/q/1/1234567890abcdef")
    for row in mat:
        print(''.join('██' if v else '  ' for v in row))
