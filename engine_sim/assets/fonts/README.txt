Bundled UI fonts
================

Any .ttf / .otf placed in this folder is embedded into the app and used for the
English UI even on machines where the font is NOT installed.  The first file
(alphabetical) is used.

Shipped here:
  Rajdhani-SemiBold.ttf  — a condensed, squarish, industrial gothic face used
  as the English UI font (a free, redistributable BankGothic-style alternative).
  NotoSansSC-Regular.otf — a clean modern Simplified-Chinese face used for the
  Chinese UI, so it looks right even without PingFang/YaHei installed.
  Both are licensed under the SIL Open Font License 1.1 and redistributable.

  English picks the first non-CJK font here; Chinese picks the first CJK font
  (name contains noto / han / pingfang / yahei / -sc ...).

To use the real BankGothic Pro instead, drop "BankGothicPro.ttf" in here and
delete (or rename) Rajdhani — the loader will pick the alphabetically-first
font file.  (BankGothic Pro is a commercial font and cannot be shipped here.)
