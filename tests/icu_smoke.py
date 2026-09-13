"""Native ICU 77.1 data/API smoke probe; not UI, physical-input or WASM coverage."""
import argparse
import ctypes as c
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eng"))
import provenance as p


def probe(package, directory, expected_sha256):
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    if digest.lower() != expected_sha256.lower():
        raise ValueError("Package SHA-256 mismatch")
    directory.mkdir(parents=True, exist_ok=False)
    if sys.platform == "win32":
        if c.sizeof(c.c_void_p) != 8:
            raise ValueError("This probe requires x64 Python on Windows")
        library = "icuuc77.dll"
        native = {f"runtimes/win-x64/native/{name}": name
                  for name in ("icuuc77.dll", "icudt77.dll")}
    elif sys.platform == "darwin":
        library = "libicuuc.dylib"
        native = {f"runtimes/osx/native/{name}": name
                  for name in ("libicuuc.dylib", "libicudata.dylib")}
    else:
        raise ValueError("This package probe supports Windows x64 or macOS only")
    native["buildTransitive/icudt.dat"] = "icudt.dat"
    with zipfile.ZipFile(package) as archive:
        for entry, name in native.items():
            p.write_new(directory / name, archive.read(entry))
    search = os.add_dll_directory(str(directory)) if sys.platform == "win32" else None
    try:
        # ICU's macOS load command refers to libicudata.77.dylib. Load the
        # packaged stub by its full path first; dyld then knows that install ID.
        data_library = c.CDLL(str(directory / "libicudata.dylib"), mode=c.RTLD_GLOBAL) if sys.platform == "darwin" else None
        icu = c.CDLL(str(directory / library))
        def function(name, result, *args):
            fn = getattr(icu, f"{name}_77")
            fn.restype = result
            fn.argtypes = args
            return fn

        status = c.c_int32()
        data = c.create_string_buffer((directory / "icudt.dat").read_bytes())
        function("udata_setCommonData", None, c.c_void_p, c.POINTER(c.c_int32))(data, c.byref(status))
        if status.value > 0:
            raise ValueError(f"ICU data registration failed: {status.value}")
        version = (c.c_uint8 * 4)()
        function("u_getVersion", None, c.POINTER(c.c_uint8))(version)
        if tuple(version) != (77, 1, 0, 0):
            raise ValueError(f"Unexpected native ICU version: {list(version)}")
        ures_open = function("ures_open", c.c_void_p, c.c_char_p, c.c_char_p, c.POINTER(c.c_int32))
        ures_close = function("ures_close", None, c.c_void_p)
        actual_locale = function("ures_getLocaleByType", c.c_char_p, c.c_void_p, c.c_int, c.POINTER(c.c_int32))
        locales = json.loads((p.ROOT / "src/cldr_data/filters.json").read_text())["localeFilter"]["whitelist"]
        culture_results = []
        for locale in locales:
            status.value = 0
            resource = ures_open(None, locale.encode(), c.byref(status))
            if not resource or status.value > 0:
                raise ValueError(f"Missing filtered culture data: {locale}, status {status.value}")
            try:
                status.value = 0
                actual = actual_locale(resource, 0, c.byref(status)).decode()
                if status.value > 0 or actual != locale:
                    raise ValueError(f"Unexpected locale fallback: {locale} -> {actual}")
                culture_results.append({"requested": locale, "actual": actual})
            finally:
                ures_close(resource)
        breaker_open = function("ubrk_open", c.c_void_p, c.c_int, c.c_char_p,
                                c.POINTER(c.c_uint16), c.c_int32, c.POINTER(c.c_int32))
        breaker_next = function("ubrk_next", c.c_int32, c.c_void_p)
        breaker_first = function("ubrk_first", c.c_int32, c.c_void_p)
        breaker_close = function("ubrk_close", None, c.c_void_p)
        cases = {
            "en": "Hello world. Next line.",
            "ar": "مرحبا بالعالم",
            "he": "שלום עולם",
            "hi": "नमस्ते दुनिया",
            "th": "ภาษาไทยภาษาไทย",
            "zh_Hans": "你好世界你好世界",
            "ja": "こんにちは世界",
            "km": "ភាសាខ្មែរភាសាខ្មែរ",
        }
        breaks = {}
        for locale, text in cases.items():
            raw = text.encode("utf-16-le")
            chars = (c.c_uint16 * (len(raw) // 2)).from_buffer_copy(raw)
            status.value = 0
            breaker = breaker_open(2, locale.encode(), chars, len(chars), c.byref(status))  # UBRK_LINE
            if not breaker or status.value > 0:
                raise ValueError(f"Line breaker failed: {locale}, {status.value}")
            try:
                positions = [breaker_first(breaker)]
                for _ in range(len(chars) + 2):
                    position = breaker_next(breaker)
                    if position == -1:
                        break
                    positions.append(position)
                else:
                    raise ValueError("Nonterminating line breaker")
                if positions[0] != 0 or positions[-1] != len(chars):
                    raise ValueError(f"Incomplete line segmentation: {locale}")
                if positions != sorted(set(positions)) or len(positions) < 3:
                    raise ValueError(f"Missing internal line opportunities: {locale}, {positions}")
                breaks[locale] = positions
            finally:
                breaker_close(breaker)
        script_of = function("uscript_getScript", c.c_int, c.c_int32, c.POINTER(c.c_int32))
        script_name = function("uscript_getName", c.c_char_p, c.c_int)
        scripts = {}
        for char, expected in (("A", "Latin"), ("ع", "Arabic"), ("א", "Hebrew"),
                               ("क", "Devanagari"), ("中", "Han"), ("𐐀", "Deseret")):
            status.value = 0
            actual = script_name(script_of(ord(char), c.byref(status))).decode()
            if status.value > 0 or actual != expected:
                raise ValueError(f"Script property mismatch: U+{ord(char):04X}")
            scripts[f"U+{ord(char):04X}"] = actual
        bidi_open = function("ubidi_open", c.c_void_p)
        bidi_close = function("ubidi_close", None, c.c_void_p)
        bidi_set = function("ubidi_setPara", None, c.c_void_p, c.POINTER(c.c_uint16),
                            c.c_int32, c.c_uint8, c.c_void_p, c.POINTER(c.c_int32))
        bidi_direction = function("ubidi_getDirection", c.c_int, c.c_void_p)
        bidi = bidi_open()
        if not bidi:
            raise ValueError("Bidi allocation failed")
        try:
            raw = "abc שלום 123".encode("utf-16-le")
            chars = (c.c_uint16 * (len(raw) // 2)).from_buffer_copy(raw)
            status.value = 0
            bidi_set(bidi, chars, len(chars), 0, None, c.byref(status))
            direction = bidi_direction(bidi)
            if status.value > 0 or direction != 2:  # UBIDI_MIXED
                raise ValueError("Mixed LTR/RTL paragraph did not resolve as mixed")
        finally:
            bidi_close(bidi)
        return {
            "packageSha256": digest, "nativeVersion": list(version),
            "dataSha256": p.sha((directory / "icudt.dat").read_bytes()),
            "cultureCount": len(culture_results), "cultures": culture_results,
            "lineBreakCases": breaks, "scripts": scripts, "bidiDirection": direction,
            "coverage": "native data loading, exact locale resolution, line segmentation, script properties, mixed bidi",
            "notCovered": ["WASM execution", "ARM64 Windows", "GUI/physical input", "full ICU conformance"],
        }
    finally:
        if search is not None:
            search.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--work-directory", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe(args.package.resolve(), args.work_directory.resolve(), args.expected_sha256)
    p.write_new(args.output, p.json_bytes(result))
    print(json.dumps(result, indent=2, ensure_ascii=False))
