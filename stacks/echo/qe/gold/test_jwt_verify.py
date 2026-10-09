"""Consumer token verification on the gold retrieval API.

The keypair below is a throwaway generated for these tests. Holding the
PRIVATE exponent is the point: it lets a test sign a deliberately malformed
PKCS#1 block, which is the only way to prove the padding check is not fooled
by one. Verified against a live issuer token during development; that cannot
be a test because the token expires.
"""

from __future__ import annotations

import base64
import json
import types

import gold_serving_api as api
import jwt_verify as jv
import pytest

TEST_N = 99842782758340164896174125751736742252189800390489235479676547795747599592969948861962637672357024944957414627337898483130501392550998250251215228310104634036125788768724328273215425502578056792248688837812440223762971325835164724787437795043011166635617671275237311658491105960805711340146005338076746848647
TEST_E = 65537
TEST_D = 84195358834283344571035586520522495648405046132425623510685629346472783909929920408398723371537960855822153543715096778441037733810267014028467290351804958798203349311361963986112028480338286078072247000196755535133688668539295759158842852370448547110545324348196560677088479019925027102685306287696479129473

SIZE = (TEST_N.bit_length() + 7) // 8
KID = "test-key-1"
ISSUER = "IES"
SUBJECT = "ffffffff5be1a0d662817d79c40f6de6"


def _clock(now):
    return types.SimpleNamespace(time=lambda: now)


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def int_b64u(value: int) -> str:
    return b64u(value.to_bytes((value.bit_length() + 7) // 8, "big"))


KEY = {"kty": "RSA", "kid": KID, "alg": "RS512",
       "n": int_b64u(TEST_N), "e": int_b64u(TEST_E)}


def seg(obj: dict) -> str:
    return b64u(json.dumps(obj, separators=(",", ":")).encode())


def sign_block(block: bytes) -> str:
    """Raw signing primitive — the block is NOT padded for you, so a test can
    hand it a malformed one."""
    return b64u(pow(int.from_bytes(block, "big"), TEST_D, TEST_N)
                .to_bytes(SIZE, "big"))


def digest_info(signing_input: str, alg: str = "RS512") -> bytes:
    return (jv._DER_PREFIX[alg]
            + jv._DIGEST[alg](signing_input.encode()).digest())


def token(claims: dict | None = None, *, alg: str = "RS512",
          kid: str | None = KID, block=None) -> str:
    header = {"alg": alg}
    if kid is not None:
        header["kid"] = kid
    payload = {"sub": SUBJECT, "iss": ISSUER, "type": "at",
               "exp": 2000000000, "iat": 1000000000}
    payload.update(claims or {})
    h, p = seg(header), seg(payload)
    # An alg with no RSA digest prefix (HS256, none) gets a well-formed RS512
    # signature: it must be refused at the alg check, before the signature is
    # ever looked at, so which bytes are there does not matter.
    info = digest_info(f"{h}.{p}", alg if alg in jv._DER_PREFIX else "RS512")
    if block is None:
        block = (b"\x00\x01" + b"\xff" * (SIZE - 3 - len(info))
                 + b"\x00" + info)
    elif callable(block):
        block = block(info)
    return f"{h}.{p}.{sign_block(block)}"


# ── Signature ───────────────────────────────────────────────────────────────

def test_a_correctly_signed_token_VERIFIES():
    claims = jv.verify_signature(token(), KEY)
    assert claims["sub"] == SUBJECT


def test_a_FORGED_pkcs1_block_with_the_digest_not_last_is_REJECTED():
    """The Bleichenbacher 2006 forgery: a parser that walks past the padding
    and reads the first DigestInfo it finds accepts this. Rebuilding the whole
    block and comparing it cannot."""
    def forged(info):
        trailing = SIZE - 3 - 8 - len(info)
        return (b"\x00\x01" + b"\xff" * 8 + b"\x00" + info
                + b"\x41" * trailing)

    with pytest.raises(jv.TokenInvalid, match="signature does not verify"):
        jv.verify_signature(token(block=forged), KEY)


def test_a_FORGED_block_with_zero_padding_bytes_is_REJECTED():
    def forged(info):
        return (b"\x00\x01" + b"\x00" + info
                + b"\x00" * (SIZE - 3 - len(info)))

    with pytest.raises(jv.TokenInvalid, match="signature does not verify"):
        jv.verify_signature(token(block=forged), KEY)


def test_a_block_using_0xff_where_the_separator_belongs_is_REJECTED():
    def forged(info):
        return b"\x00\x01" + b"\xff" * (SIZE - 2 - len(info)) + info

    with pytest.raises(jv.TokenInvalid, match="signature does not verify"):
        jv.verify_signature(token(block=forged), KEY)


def test_alg_none_is_REJECTED():
    h = seg({"alg": "none", "kid": KID})
    p = seg({"sub": SUBJECT})
    with pytest.raises(jv.TokenInvalid, match="not allowed"):
        jv.verify_signature(f"{h}.{p}.", KEY)


def test_an_HMAC_alg_is_REJECTED():
    """Alg confusion: HS256 would make the RSA public key the shared secret,
    and the public key is public."""
    with pytest.raises(jv.TokenInvalid, match="not allowed"):
        jv.verify_signature(token(alg="HS256"), KEY)


def test_an_alg_the_KEY_does_not_declare_is_REJECTED():
    """A caller rewriting RS512 to RS256 in the header must not downgrade the
    digest, because the key pins the algorithm."""
    with pytest.raises(jv.TokenInvalid, match="does not match the key"):
        jv.verify_signature(token(alg="RS256"), KEY)


def test_an_alg_outside_the_CALLERS_allow_list_is_REJECTED():
    with pytest.raises(jv.TokenInvalid, match="not allowed"):
        jv.verify_signature(token(), KEY, allowed_algs=frozenset({"RS256"}))


def test_a_TAMPERED_payload_is_REJECTED():
    good = token()
    h, _, s = good.split(".")
    forged = seg({"sub": "somebody-else", "iss": ISSUER, "type": "at",
                  "exp": 2000000000})
    with pytest.raises(jv.TokenInvalid, match="does not verify"):
        jv.verify_signature(f"{h}.{forged}.{s}", KEY)


def test_a_signature_of_the_wrong_LENGTH_is_REJECTED():
    h, p, s = token().split(".")
    short = b64u(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))[:-4])
    with pytest.raises(jv.TokenInvalid, match="length does not match"):
        jv.verify_signature(f"{h}.{p}.{short}", KEY)


def test_a_token_that_is_not_three_segments_is_REJECTED():
    with pytest.raises(jv.TokenInvalid, match="three segments"):
        jv.verify_signature("only.two", KEY)


def test_an_oversized_token_is_REJECTED_before_any_modexp():
    with pytest.raises(jv.TokenInvalid, match="too large"):
        jv.verify_signature("a" * (jv.MAX_TOKEN_BYTES + 1), KEY)


def test_a_non_RSA_key_is_REJECTED():
    with pytest.raises(jv.TokenInvalid, match="not RSA"):
        jv.verify_signature(token(), dict(KEY, kty="EC"))


def test_unparseable_base64_is_REJECTED():
    with pytest.raises(jv.TokenInvalid):
        jv.verify_signature("!!!.!!!.!!!", KEY)


def test_a_header_with_no_kid_is_REJECTED():
    with pytest.raises(jv.TokenInvalid, match="no kid"):
        jv.header_kid(token(kid=None))


def test_header_kid_is_read_without_verifying():
    assert jv.header_kid(token()) == KID


# ── Claims ──────────────────────────────────────────────────────────────────

def claims(**over) -> dict:
    base = {"sub": SUBJECT, "iss": ISSUER, "type": "at",
            "exp": 2000000000, "iat": 1000000000}
    base.update(over)
    return base


def check(c, **over):
    kwargs = {"issuer": ISSUER, "allowed_subs": [SUBJECT],
              "token_type": "at", "now": 1500000000}
    kwargs.update(over)
    return jv.check_claims(c, **kwargs)


def test_valid_claims_PASS():
    assert check(claims())["sub"] == SUBJECT


def test_a_wrong_ISSUER_is_REJECTED():
    with pytest.raises(jv.TokenInvalid, match="issuer"):
        check(claims(iss="SOMEONE-ELSE"))


def test_a_wrong_token_TYPE_is_REJECTED():
    """A refresh token must not be usable as an access token."""
    with pytest.raises(jv.TokenInvalid, match="token type"):
        check(claims(type="rt"))


def test_a_MISSING_exp_is_REJECTED():
    c = claims()
    del c["exp"]
    with pytest.raises(jv.TokenInvalid, match="no exp"):
        check(c)


def test_a_non_numeric_exp_is_REJECTED():
    with pytest.raises(jv.TokenInvalid, match="no exp"):
        check(claims(exp="2000000000"))


def test_an_EXPIRED_token_is_REJECTED():
    with pytest.raises(jv.TokenInvalid, match="expired"):
        check(claims(), now=2000000061)


def test_an_expiry_inside_the_LEEWAY_still_passes():
    assert check(claims(), now=2000000030)["sub"] == SUBJECT


def test_an_iat_in_the_FUTURE_is_REJECTED():
    with pytest.raises(jv.TokenInvalid, match="iat is in the future"):
        check(claims(iat=1600000000), now=1500000000)


def test_an_nbf_in_the_FUTURE_is_REJECTED():
    with pytest.raises(jv.TokenInvalid, match="nbf is in the future"):
        check(claims(nbf=1600000000), now=1500000000)


def test_a_subject_NOT_on_the_allow_list_is_FORBIDDEN():
    with pytest.raises(jv.SubjectNotAllowed):
        check(claims(), allowed_subs=["someone-else"])


def test_an_EMPTY_allow_list_admits_nobody():
    with pytest.raises(jv.SubjectNotAllowed):
        check(claims(), allowed_subs=[])


def test_a_missing_sub_is_FORBIDDEN():
    c = claims()
    del c["sub"]
    with pytest.raises(jv.SubjectNotAllowed):
        check(c)


def test_an_INVALID_token_for_a_disallowed_subject_reports_the_TOKEN_fault():
    """Ordering matters: a 403 must only ever follow a token that is otherwise
    entirely valid, or a 403 becomes a way to probe which subjects exist."""
    with pytest.raises(jv.TokenInvalid):
        check(claims(iss="WRONG"), allowed_subs=["someone-else"])


# ── Bearer header ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["Bearer abc", "bearer abc", "BEARER abc",
                                   "Bearer   abc  "])
def test_a_bearer_header_yields_the_token(value):
    assert jv.bearer({"authorization": value}) == "abc"


@pytest.mark.parametrize("headers", [{}, {"authorization": ""},
                                     {"authorization": "Bearer"},
                                     {"authorization": "Bearer   "},
                                     {"authorization": "Basic abc"},
                                     {"authorization": "abc"}])
def test_a_header_without_a_usable_bearer_token_is_REJECTED(headers):
    with pytest.raises(jv.TokenInvalid, match="no bearer token"):
        jv.bearer(headers)


# ── JWKS cache ──────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_urlopen(payload, counter, fail=False):
    def _open(request, timeout=None):
        counter.append(1)
        if fail:
            raise OSError("issuer unreachable")
        return FakeResponse(payload)
    return _open


def test_the_cache_fetches_a_key_once_and_reuses_it(monkeypatch):
    calls = []
    monkeypatch.setattr(jv.urllib.request, "urlopen",
                        fake_urlopen({"keys": [KEY]}, calls))
    cache = jv.JwksCache("https://issuer.invalid/jwks.json")
    assert cache.key(KID, now=1000.0)["kid"] == KID
    assert cache.key(KID, now=1001.0)["kid"] == KID
    assert len(calls) == 1


def test_the_cache_REFETCHES_after_its_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(jv.urllib.request, "urlopen",
                        fake_urlopen({"keys": [KEY]}, calls))
    cache = jv.JwksCache("https://issuer.invalid/jwks.json", ttl=100)
    cache.key(KID, now=1000.0)
    cache.key(KID, now=1101.0)
    assert len(calls) == 2


def test_a_failed_refresh_serves_the_STALE_key(monkeypatch, capsys):
    """Failing closed on the issuer's availability would make every cold start
    depend on it. A cached key is still a valid key."""
    calls = []
    monkeypatch.setattr(jv.urllib.request, "urlopen",
                        fake_urlopen({"keys": [KEY]}, calls))
    cache = jv.JwksCache("https://issuer.invalid/jwks.json", ttl=100)
    cache.key(KID, now=1000.0)
    monkeypatch.setattr(jv.urllib.request, "urlopen",
                        fake_urlopen(None, calls, fail=True))
    assert cache.key(KID, now=1200.0)["kid"] == KID
    assert "JWKS refresh failed" in capsys.readouterr().out


def test_no_keys_at_all_and_a_failed_fetch_REFUSES(monkeypatch):
    calls = []
    monkeypatch.setattr(jv.urllib.request, "urlopen",
                        fake_urlopen(None, calls, fail=True))
    cache = jv.JwksCache("https://issuer.invalid/jwks.json")
    with pytest.raises(jv.TokenInvalid, match="unknown signing key"):
        cache.key(KID, now=1000.0)


def test_an_unknown_kid_cannot_be_used_to_HAMMER_the_issuer(monkeypatch):
    """Otherwise a caller sending random kids turns this endpoint into traffic
    against the identity provider."""
    calls = []
    monkeypatch.setattr(jv.urllib.request, "urlopen",
                        fake_urlopen({"keys": [KEY]}, calls))
    cache = jv.JwksCache("https://issuer.invalid/jwks.json",
                         min_refresh_seconds=60)
    cache.key(KID, now=1000.0)
    for n in range(20):
        with pytest.raises(jv.TokenInvalid):
            cache.key("rotating-kid", now=1000.0 + n)
    assert len(calls) == 1


def test_a_key_without_a_kid_is_ignored_rather_than_crashing(monkeypatch):
    calls = []
    monkeypatch.setattr(jv.urllib.request, "urlopen",
                        fake_urlopen({"keys": [{"kty": "RSA"}, KEY]}, calls))
    cache = jv.JwksCache("https://issuer.invalid/jwks.json")
    assert cache.key(KID, now=1000.0)["kid"] == KID


# ── The Authorizer: configuration and the decision ─────────────────────────

class StubKeys:
    """Stands in for JwksCache so these tests exercise the DECISION, not the
    fetch — the cache has its own tests above."""

    def __init__(self, keys):
        self._keys = keys

    def key(self, kid, now=None):
        if kid not in self._keys:
            raise jv.TokenInvalid("unknown signing key")
        return self._keys[kid]


def authorizer(**over):
    kwargs = {"required": True, "issuer": ISSUER,
              "jwks_url": "https://issuer.invalid/jwks.json",
              "allowed_subs": {SUBJECT}, "token_type": "at",
              "algs": {"RS512"}, "jwks": StubKeys({KID: KEY}),
              "label": "gold-serving-api"}
    kwargs.update(over)
    return jv.Authorizer(**kwargs)


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setattr(api, "_auth", authorizer())
    monkeypatch.setattr(jv, "time", _clock(1500000000))


def test_a_valid_token_for_an_allowed_subject_is_ADMITTED(armed):
    assert api._auth.check({"authorization": f"Bearer {token()}"}) is None


def test_no_token_is_401_with_a_www_authenticate_header(armed):
    denial = api._auth.check({})
    assert denial.status == 401
    assert denial.headers["www-authenticate"] == "Bearer"


def test_an_expired_token_is_401(armed, monkeypatch):
    monkeypatch.setattr(jv, "time", _clock(2000000061))
    assert api._auth.check(
        {"authorization": f"Bearer {token()}"}).status == 401


def test_a_valid_token_for_another_subject_is_403(armed):
    auth = authorizer(allowed_subs={"someone-else"})
    denial = auth.check({"authorization": f"Bearer {token()}"})
    assert (denial.status, denial.error) == (403, "subject not authorized")


def test_a_401_does_not_disclose_WHY(armed):
    """The reason goes to the log, not the caller: telling an attacker whether
    the signature or the expiry failed tells them which half to work on."""
    assert api._auth.check(
        {"authorization": "Bearer a.b.c"}).error == "unauthorized"


def test_the_reason_IS_logged(armed, capsys):
    api._auth.check({"authorization": "Bearer a.b.c"})
    out = capsys.readouterr().out
    assert "JWT_AUTH_TOKEN_REJECTED" in out
    # The label is what lets one alarm tell the two APIs apart.
    assert "gold-serving-api" in out


def test_switched_ON_but_UNCONFIGURED_refuses_every_request():
    """The posture the shared-header check gets wrong: unconfigured must not
    mean unprotected."""
    auth = jv.Authorizer(required=True)
    denial = auth.check({"authorization": "Bearer whatever"})
    assert (denial.status, denial.error) == (500, "authorization misconfigured")


@pytest.mark.parametrize("missing,empty", [("issuer", ""), ("jwks_url", ""),
                                           ("allowed_subs", set())])
def test_ANY_missing_setting_refuses(missing, empty):
    auth = authorizer(**{missing: empty})
    assert auth.check({"authorization": "Bearer whatever"}).status == 500


def test_switched_OFF_admits_everything():
    assert jv.Authorizer(required=False).check({}) is None


def test_from_env_DEFAULTS_the_check_ON():
    """A deployment that forgets the variables must refuse, not serve data
    openly. Read from an EMPTY mapping, so this tests the default rather than
    whatever happens to be in the developer's environment."""
    auth = jv.Authorizer.from_env(env={})
    assert auth.required is True
    assert auth.configured is False


def test_from_env_reads_every_setting():
    auth = jv.Authorizer.from_env(label="x", env={
        "JWT_REQUIRED": "true", "JWT_ISSUER": "IES",
        "JWT_JWKS_URL": "https://i.invalid/jwks.json",
        "JWT_TOKEN_TYPE": "at", "JWT_ALGS": "RS512,RS256",
        "JWT_ALLOWED_SUBS": f" {SUBJECT} , second "})
    assert auth.issuer == "IES"
    assert auth.token_type == "at"
    assert auth.algs == {"RS512", "RS256"}
    assert auth.allowed_subs == {SUBJECT, "second"}
    assert auth.configured is True


def test_from_env_treats_an_empty_token_type_as_NO_check():
    assert jv.Authorizer.from_env(env={"JWT_TOKEN_TYPE": ""}).token_type is None


def test_from_env_is_switched_off_only_by_an_explicit_false():
    for value in ("false", "FALSE", "False"):
        assert jv.Authorizer.from_env(env={"JWT_REQUIRED": value}).required is False
    for value in ("true", "", "yes", "0", "no"):
        assert jv.Authorizer.from_env(env={"JWT_REQUIRED": value}).required is True


# ── Both handlers actually call it ──────────────────────────────────────────
# Without these, deleting the `_auth.check(...)` call from a handler leaves
# every test above green and that endpoint open. They use the sibling module's
# AWS fakes rather than a second set, so the read path they fall through to is
# the real one.

import importlib.util
import pathlib

# By PATH, not by name: this suite runs under pytest's importlib mode, where a
# sibling test module is not on sys.path.
_sibling = pathlib.Path(__file__).with_name("test_gold_serving_api.py")
_spec = importlib.util.spec_from_file_location("_serving_api_fakes", _sibling)
_fakes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fakes)

HDR, SECRET = _fakes.HDR, _fakes.SECRET
FakeDdb, FakeS3, FakeSecrets = _fakes.FakeDdb, _fakes.FakeS3, _fakes.FakeSecrets


def call(headers=None):
    event = {"rawPath": "/stock/A1",
             "requestContext": {"http": {"method": "GET"}},
             "headers": {HDR: SECRET, **(headers or {})}}
    return api.handler(event, None, ddb=FakeDdb(), s3=FakeS3(),
                       secrets=FakeSecrets())


def test_the_gold_HANDLER_refuses_a_request_with_no_token(armed):
    assert call()["statusCode"] == 401


def test_the_gold_HANDLER_admits_a_valid_token(armed):
    assert call({"authorization": f"Bearer {token()}"})["statusCode"] == 200


def test_the_gold_HANDLER_forbids_a_valid_token_for_another_subject(
        armed, monkeypatch):
    monkeypatch.setattr(api, "_auth", authorizer(allowed_subs={"nobody"}))
    assert call({"authorization": f"Bearer {token()}"})["statusCode"] == 403


def test_the_gold_HANDLER_refuses_when_armed_but_unconfigured(monkeypatch):
    monkeypatch.setattr(api, "_auth", jv.Authorizer(required=True))
    assert call()["statusCode"] == 500


def test_the_gold_HANDLER_still_serves_when_the_check_is_OFF(monkeypatch):
    monkeypatch.setattr(api, "_auth", jv.Authorizer(required=False))
    assert call()["statusCode"] == 200


def test_BOTH_apis_build_their_authorizer_from_the_same_variables():
    """One identity configuration for the estate. A second API inventing its
    own variable names is how two surfaces end up authorizing differently."""
    gold = pathlib.Path(api.__file__).read_text(encoding="utf-8")
    mcopy = (pathlib.Path(api.__file__).resolve().parents[5]
             / "domains/product/src/apps/lambda_marketing_copy_api"
             / "marketing_copy_api.py").read_text(encoding="utf-8")
    for source in (gold, mcopy):
        assert "jwt_verify.Authorizer.from_env(" in source
        assert "_auth.check(headers)" in source
