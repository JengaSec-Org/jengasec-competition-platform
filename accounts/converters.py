"""A URL converter shaped like the tokens this app actually issues.

`<str:token>` matches any segment without a slash, which made
`invitations/<str:token>/<str:decision>/` swallow
`invitations/revoke/2/` as token="revoke", decision="2" -- so revoking
an invitation answered 404 from a view that was never meant to run.
Reordering the routes fixes that one URL and leaves the trap set for the
next literal segment added under `invitations/`.

Constraining the token instead closes the whole class: tokens are
`secrets.token_urlsafe(32)`, which is 43 characters of URL-safe base64,
so no readable word can be mistaken for one.
"""


class TokenConverter:
    # secrets.token_urlsafe(32) yields 43 chars; the band is wide enough
    # for a different length later and far above any path segment.
    regex = r"[A-Za-z0-9_-]{20,64}"

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value
