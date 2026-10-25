# Brand credential directory names

`BrandCredentials(brand_id, credentials_dir)` accepts a brand identifier as one
directory name beneath the configured credentials directory. Letters, numbers,
underscores, hyphens and embedded dots continue to work; for example,
`Brand_1.v2-test`.

Empty identifiers, `.` and `..`, either path separator, Windows drive syntax,
and control characters are rejected with `ValueError` before a key or directory
is created. An existing brand-directory symlink is also rejected, including
one pointing at another brand. Put the root path in `credentials_dir`, not in
`brand_id`.

Existing valid brand names, encrypted file formats and legacy credential
fallback behavior are unchanged. The configured credential tree must remain
under trusted local control; this validation is not protection against another
process changing filesystem links concurrently.
