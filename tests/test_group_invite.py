from __future__ import annotations

import pytest

from i2pchat import crypto
from i2pchat.groups.invite import (
    GROUP_INVITE_PREFIX,
    _seal_invite_plaintext,
    _unseal_invite_plaintext,
    build_group_invite,
    decode_group_invite,
    encode_group_invite,
    looks_like_group_invite,
)
from i2pchat.presentation.group_conversations import render_group_control_text

ALICE = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BOB = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
CAROL = "cccccccccccccccccccccccccccccccccccccccc"


def _seed() -> bytes:
    seed, _ = crypto.generate_signing_keypair()
    return seed


def test_group_invite_round_trip() -> None:
    seed = _seed()
    invite = build_group_invite(
        group_id="group-abc",
        members=(ALICE, BOB),
        epoch=3,
        inviter_id=ALICE,
        title="Weekend",
        invite_id="deadbeef",
    )
    encoded = encode_group_invite(invite, seed)
    assert not encoded.startswith(GROUP_INVITE_PREFIX)
    assert "{" not in encoded
    assert "Weekend" not in encoded
    assert ALICE not in encoded
    assert BOB not in encoded
    assert "group-abc" not in encoded
    assert "inviter_id" not in encoded
    assert looks_like_group_invite(encoded) is True

    decoded = decode_group_invite(encoded)
    assert decoded.group_id == "group-abc"
    assert decoded.title == "Weekend"
    assert decoded.epoch == 3
    assert decoded.inviter_id == ALICE
    assert decoded.members == (ALICE, BOB)
    assert decoded.invite_id == "deadbeef"
    assert decoded.inviter_signing_pub == crypto.get_verify_key_from_seed(seed).hex()
    assert decoded.signature


def test_group_invite_round_trip_ignores_wrapping_whitespace() -> None:
    seed = _seed()
    encoded = encode_group_invite(
        build_group_invite(
            group_id="g",
            members=(ALICE,),
            epoch=1,
            inviter_id=ALICE,
            title="ILITA",
        ),
        seed,
    )
    wrapped = "\n".join(encoded[i : i + 40] for i in range(0, len(encoded), 40))
    decoded = decode_group_invite(wrapped)
    assert decoded.title == "ILITA"
    assert looks_like_group_invite(wrapped) is True


def test_encode_requires_signing_seed() -> None:
    invite = build_group_invite(
        group_id="g", members=(ALICE,), epoch=0, inviter_id=ALICE
    )
    with pytest.raises(ValueError, match="signing seed"):
        encode_group_invite(invite, b"")


def test_decode_rejects_tampered_sealed_blob() -> None:
    seed = _seed()
    encoded = encode_group_invite(
        build_group_invite(group_id="g", members=(ALICE, BOB), epoch=1, inviter_id=ALICE),
        seed,
    )
    flipped = encoded[:-1] + ("A" if encoded[-1] != "A" else "B")
    with pytest.raises(ValueError, match="decryption failed|Invalid group invite"):
        decode_group_invite(flipped)


def test_decode_rejects_tampered_members_inside_seal() -> None:
    seed = _seed()
    encoded = encode_group_invite(
        build_group_invite(group_id="g", members=(ALICE, BOB), epoch=1, inviter_id=ALICE),
        seed,
    )
    inner = _unseal_invite_plaintext(encoded).decode("utf-8")
    tampered_inner = inner.replace(BOB, CAROL).encode("utf-8")
    resealed = _seal_invite_plaintext(tampered_inner)
    with pytest.raises(ValueError, match="signature verification failed"):
        decode_group_invite(resealed)


def test_decode_rejects_v1_unsigned_invite() -> None:
    # A legacy v1 (unsigned) invite payload must be rejected outright.
    legacy = GROUP_INVITE_PREFIX + (
        '{"v":1,"invite_id":"x","group_id":"g","title":null,'
        '"members":["' + ALICE + '"],"epoch":0,"inviter_id":"' + ALICE + '",'
        '"created_at":"2020-01-01T00:00:00+00:00"}'
    )
    with pytest.raises(ValueError, match="version"):
        decode_group_invite(legacy)


def test_decode_accepts_legacy_signed_v2_prefix_form() -> None:
    seed = _seed()
    invite = build_group_invite(
        group_id="legacy-g",
        members=(ALICE, BOB),
        epoch=2,
        inviter_id=ALICE,
        title="Old",
        invite_id="cafebabe",
    )
    sealed = encode_group_invite(invite, seed)
    inner = _unseal_invite_plaintext(sealed).decode("utf-8")
    legacy = GROUP_INVITE_PREFIX + inner
    decoded = decode_group_invite(legacy)
    assert decoded.title == "Old"
    assert decoded.group_id == "legacy-g"


def test_decode_group_invite_rejects_malformed() -> None:
    seed = _seed()
    with pytest.raises(ValueError, match="Not a group invite"):
        decode_group_invite("__I2PCHAT_GROUP__:{}")
    with pytest.raises(ValueError, match="Empty group invite"):
        decode_group_invite(GROUP_INVITE_PREFIX)
    with pytest.raises(ValueError, match="Invalid group invite JSON"):
        decode_group_invite(GROUP_INVITE_PREFIX + "{not-json")
    inner = _unseal_invite_plaintext(
        encode_group_invite(
            build_group_invite(
                group_id="x",
                members=(ALICE,),
                epoch=0,
                inviter_id=ALICE,
            ),
            seed,
        )
    ).decode("utf-8")
    with pytest.raises(ValueError, match="group_id"):
        decode_group_invite(_seal_invite_plaintext(inner.replace('"group_id":"x"', '"group_id":""').encode("utf-8")))


def test_looks_like_group_invite_ignores_destinations() -> None:
    assert looks_like_group_invite("") is False
    assert looks_like_group_invite(ALICE) is False
    assert looks_like_group_invite("hello world") is False


def test_build_group_invite_ensures_inviter_in_members() -> None:
    invite = build_group_invite(
        group_id="g1",
        members=(BOB,),
        epoch=1,
        inviter_id=ALICE,
    )
    assert ALICE in invite.members
    assert BOB in invite.members


def test_render_group_control_text_join_op() -> None:
    text = render_group_control_text(
        {
            "op": "join",
            "joined_member_id": CAROL,
            "members": [ALICE, BOB, CAROL],
            "epoch": 4,
        },
        actor_label="Carol",
    )
    assert "joined the group" in text
    assert "updated group settings" not in text
