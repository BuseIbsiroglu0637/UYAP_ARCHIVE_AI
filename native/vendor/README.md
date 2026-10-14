# Vendored libraries

The portable agent runs on an embedded Python with no pip, so pure-Python
libraries it needs are kept here and copied to `runtime\vendor` by
`native/portable_installer.ps1`. `ai_local_index.py` puts this folder first on
`sys.path`.

| Library | Version | License | Used for |
|---|---|---|---|
| pypdf | 6.20.1 | BSD-3-Clause (`pypdf-6.20.1.dist-info/licenses/LICENSE`) | PDF text with fonts / ToUnicode CMaps (`ai_local_index._pypdf_text`) |

No optional dependencies are vendored: AES-encrypted PDFs that need
`cryptography` fall back to the built-in stream reader.

## Updating

```powershell
python -m pip install --no-deps --no-compile --target $env:TEMP\vendor pypdf==<version>
```

Replace `native/vendor/pypdf` and the `pypdf-*.dist-info` folder with the new
ones, update the table above, and run `native/pdf_reader_regression_test.py`.
