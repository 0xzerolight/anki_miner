"""The 200 accent-stripped syllables the Vietnamese script gate counts (spec C.4, S15).

For a line typed without diacritics (teencode, old chat logs) the gate cannot see a
Vietnamese letter, so it counts ASCII words that are common Vietnamese syllables. Derived:
the 200 most frequent syllables, every diacritic stripped (d-stroke -> d), of the first
400,000 lines of OPUS OpenSubtitles v2024 ``vi`` (ODC-BY 1.0; P. Lison and J. Tiedemann,
2016), skipping twelve English function words that collide with a stripped syllable (a an
the to do on so no me be can may), so a one-word English deck front never reads as
unaccented Vietnamese.
"""

from __future__ import annotations

#: Space-separated so the table reads as prose; split once at import.
_TABLE = (
    "toi co khong anh la mot ta se ong da cua cho di day va con gi em chung nhung nguoi phai lam noi o "
    "duoc nay roi voi ay biet dau de dung sao vay trong lai dang nao ve muon nhu den chi cung cau ra khi "
    "ban ca ho tu gio cai chuyen thay ba moi nghi doi ma hay bao thu qua thoi chu thi vi neu cac vao bo "
    "minh that ngay nua rat dieu hon ai nha cam chua han bi su loi mat viec tai yeu bat nhan quan xin len "
    "van nhieu vang cu nghe tien tot tat thua dong hoi dua chau tam y gia nho gap hai rang bay du chao "
    "dan chang cong nhat nen bac sau cha tren nam chac tim luc vo tinh truoc ngai thanh khac dien lay oi "
    "ke tro song nhin dai than le thuc hieu tin tuong trai thich thuong dinh sang lan lo theo ha thang "
    "chan het ten cuoi danh chet duong khoi tay tra cach ngu bon goi phong xem mo ngoi bang si nhien phu "
    "kia canh xuong ngoai hanh cuoc vui chinh xe ly luon dep san dam "
)
COMMON_SYLLABLES: frozenset[str] = frozenset(_TABLE.split())
