"""Contrats de sécurité de la dépendance utilisée par les validateurs JWT."""
import base64
import time

import jwt
import pytest

KEY = "synthetic-test-key-with-32-bytes-minimum"


@pytest.mark.parametrize("method", ["decode", "decode_complete"])
@pytest.mark.parametrize("claim,error", [
    ({"exp": 1}, jwt.ExpiredSignatureError),
    ({"aud": "wrong"}, jwt.InvalidAudienceError),
    ({"iss": "wrong"}, jwt.InvalidIssuerError),
])
def test_options_reutilisees_ne_desactivent_pas_les_claims(method, claim, error):
    token = jwt.encode({"exp": int(time.time()) + 300, "aud": "expected", "iss": "expected", **claim}, KEY, algorithm="HS256")
    options = {"verify_signature": False}
    decode = getattr(jwt, method)
    decode(token, options=options)
    assert options == {"verify_signature": False}
    options["verify_signature"] = True
    with pytest.raises(error):
        decode(token, KEY, algorithms=["HS256"], options=options, audience="expected", issuer="expected")
    assert options == {"verify_signature": True}


def test_padding_legal_conserve_signature_et_junk_est_refuse():
    token = jwt.encode({"sub": "synthetic"}, KEY, algorithm="HS256")
    head, payload, signature = token.split(".")
    padded = ".".join((head, payload, signature + "=" * (-len(signature) % 4)))
    assert jwt.decode(padded, KEY, algorithms=["HS256"])["sub"] == "synthetic"
    with pytest.raises(jwt.DecodeError):
        jwt.decode(token + "!!!!", KEY, algorithms=["HS256"])


@pytest.mark.parametrize("part", ["header", "payload"])
def test_entree_profondement_imbriquee_est_decodeerror(part):
    def encode(value):
        return base64.urlsafe_b64encode(value.encode()).rstrip(b"=").decode()
    nested = '[' * 2000 + '0' + ']' * 2000
    header = nested if part == "header" else '{"alg":"HS256"}'
    payload = nested if part == "payload" else '{}'
    token = encode(header) + "." + encode(payload) + ".YWJj"
    with pytest.raises(jwt.DecodeError):
        jwt.decode(token, options={"verify_signature": False})
