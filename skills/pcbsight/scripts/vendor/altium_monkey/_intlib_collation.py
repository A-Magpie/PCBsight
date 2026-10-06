"""Pinned cross-platform collation for IntLib component graphs."""

from __future__ import annotations

import base64
import struct
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class _CharacterWeights:
    primary: bytes
    secondary: int
    tertiary: int
    quaternary: int
    special: bytes


# Altium 26 orders the shared component graph with Windows en-US word-sort
# semantics. IntLib metadata is CP1252, so a complete table for every character
# reachable by case-folding CP1252 is both smaller and more reproducible than a
# process-global locale or a platform ICU dependency. These weights were
# captured with LCMapStringEx on the validated Altium 26 host. Windows tests
# compare the pinned table against that API so a future intentional refresh is
# explicit.
_PINNED_WEIGHT_DATA = (
    "0KNbK000000000000031000020}=oL00IC2000665&!@I0{{R300IRP00004000000tONQ000F5000022ND1P00sa60006A5&!@I"
    "2LJ#700IdT00008000000tylU000RB2L%8C00000015&J1^@s6000003jzlR000000000C0tW~H00000000dF2MGWG0000001f~E"
    "0006D5&!@I4*&oF00ImW0000G000000u2%X000pH000024iW$W01^NI0006H5&!@I6951J00Iya0000K000000ud4b000#L00002"
    "5)uFa02TlM0006L5&!@I7XSbN00I;e0000O000000u>Sf000>P0000277_pe02%-Q0006P5&!@I8vp<R00I~i0000S000000vQqj"
    "0012T000028WI2i03HAU0006T5&!@I9{>OV00JBm0000W0tW&B00000001EZ2OIzZ0000003reh9RL6T00000BLW8>000000000a"
    "0tX=g00000001Qd2O|Ig00000044$lB>(^b00000CjbBd00Mv#0000e0tY7m00000001ch2Pyyn0000004f3pEdT%j00000D*^}u"
    "000000000i0tYVu00000001oj00002f)W4#04@RtGXMYp00000F9HWO000000000m0u2KI00000001!p4H^Id0000005Sp%8~^|S"
    "00000GXf1B000000000q0u3Ml00000001=t4I%&l0000005$>*Bme*a00000Hv$bN000000000u0u3kt000000021x4JrTt000000"
    "6GE(Hvj+t00000I|2tf000000000y0tgNO00000002D#2oeAQ0000006qc;6aWAK00000KLQ6l000000000$0tY?-00000003J82"
    "R{G+0000009*nGK>z>%00000T>=L}00000000190tZ6?00000003VC2Sfk>000000AK<KNB{r;00000VFC^U000000001D0uBiP"
    "00000003hG4hjGO000000AvCV8UO$Q00000WdaT%000000001H0uCbp00000003tK4kZ8p000000B8aZEC2ui00000X#x&100000"
    "0001L0uD6*00000003(O4mJP)000000BiydNB{r;00000Z2}Ha000000001P0uFEh00000003_S4txLr000000B`~hegFUf00000"
    "aRLsB000000001T0uG7*000000046W4v_!=000000CWNlnE(I)00000bpj5b000000001X0uG`800000004Ia4x|78000000C)lp"
    "rT_o{00000c>)fn000000001b0uHGF00000004Ue2TA|{000000DJ-mOaK4?00000eF6tg000000001f0tZk400000004gg00002"
    "9TETl0H6W~1ONa400000p#ld{000000001@0tc4>00000005%`2bcf=000000Hgv3nE(I)00000r2+?<000000001{0tZq600000"
    "005@~3I+fG000000H^{7Qvd(}00000sR9ZI00000000200uBNL4gdfE006532p9kW000000IUKE2mk;800000tpET3000000ImWG"
    "2><{900000uL1{D00000000260tyNM00000006N92p0eV000000I~uN8~_dg00000vjPns01f~E0002A0tZz900000006cE3Jd@M"
    "000000Jj1P4FCWD00000xB>@O000000002F0u34f4gdfE006oI4txU+000000J{PR8UO$Q00000yaEjk000000002J0u2!W00000"
    "006!M4HW<Y000000KWnUR{#J200000*8&I}000000002s0uBNX00000007|v4gwAU000000OA4;0ulfK00000;{px>82|tP0002w"
    "0uBNb000000089z4gwkg000000OkY^0uCVn00000008F#4hkFq000000O$e^ArAlm00000=>iTR4gdfE0002$0uCV(00000008R("
    "4j~f&000000PF$|G7kU%00000?E(%m4gdfE0002)0uC|~00000008d-4l)w}000000Pq418fX9j00000@d6HT82|tP0002;0uFo+"
    "00000008p>4tx#(000000Q3S5d=dZv00000^#Trj82|tP0002?0uFo=00000008#_2ps?b000000Qdq9d?5e;00000`2r4~4*&oF"
    "0002`0uG-J00000008>}4xbVL000000Q>?DpA!H800000{Q?fB4gdfE0002~1P+-FEC2ui00000{{jxD6951J0003~1P*)-ApigX"
    "00001VFC`36aWAK0004g0uHGZ0000000EK$4kJSV000000>%OdLjV8(00002+yVz(00000000BL0uLAf0000003Z_p00002i4p(+"
    "AQS)q0007z5&!@o7y<`*00000001Bv0tb2k0000003aFy2YUbj00000ARGb*eE<Le0000W9Rde_00000001B!0tbHp0000003aX&"
    "3nc&m00000ARz(^CIA2c0000WA_5C100000001B+0tyfS0000003a{|3oHx(00000AUOgDhyVZp0000WIsylY00000001DY0tcx8"
    "0000003jj*4x9iI000"
)


@lru_cache(maxsize=1)
def _character_weights() -> dict[str, _CharacterWeights]:
    data = memoryview(base64.b85decode(_PINNED_WEIGHT_DATA))
    count = struct.unpack_from(">H", data)[0]
    offset = 2
    weights: dict[str, _CharacterWeights] = {}
    for _ in range(count):
        codepoint, primary_size = struct.unpack_from(">IB", data, offset)
        offset += 5
        primary = bytes(data[offset : offset + primary_size])
        offset += primary_size
        secondary, tertiary, quaternary, special_size = struct.unpack_from(
            ">BBBB", data, offset
        )
        offset += 4
        special = bytes(data[offset : offset + special_size])
        offset += special_size
        weights[chr(codepoint)] = _CharacterWeights(
            primary=primary,
            secondary=secondary,
            tertiary=tertiary,
            quaternary=quaternary,
            special=special,
        )
    if offset != len(data):
        raise RuntimeError("invalid pinned IntLib collation table")
    return weights


def _trim_default_weights(values: list[int]) -> bytes:
    while values and values[-1] == 2:
        values.pop()
    return bytes(values)


@lru_cache(maxsize=4_096)
def intlib_name_sort_key(value: str) -> tuple[bytes, bytes, bytes, bytes, bytes, bytes]:
    """Return a deterministic Windows en-US word-sort key for CP1252 text."""
    folded = value.casefold()
    weights_by_character = _character_weights()
    primary = bytearray()
    secondary: list[int] = []
    tertiary: list[int] = []
    quaternary: list[int] = []
    special = bytearray()
    primary_count = 0
    for character in folded:
        try:
            weights = weights_by_character[character]
        except KeyError as error:
            raise ValueError(
                f"IntLib names must be representable in Windows-1252: {value!r}"
            ) from error
        element_count = len(weights.primary) // 2
        primary.extend(weights.primary)
        character_secondary = [2] * element_count
        character_tertiary = [2] * element_count
        character_quaternary = [2] * element_count
        if element_count:
            if weights.secondary:
                character_secondary[0] = weights.secondary
            if weights.tertiary:
                character_tertiary[0] = weights.tertiary
            if weights.quaternary:
                character_quaternary[0] = weights.quaternary
        secondary.extend(character_secondary)
        tertiary.extend(character_tertiary)
        quaternary.extend(character_quaternary)
        if weights.special:
            special.extend((0xFFFF - primary_count).to_bytes(2, "big"))
            special.extend(weights.special)
        primary_count += element_count
    return (
        bytes(primary),
        _trim_default_weights(secondary),
        _trim_default_weights(tertiary),
        _trim_default_weights(quaternary),
        bytes(special),
        folded.encode("utf-8"),
    )
