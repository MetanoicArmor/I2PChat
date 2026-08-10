from __future__ import annotations

import pytest

from i2pchat.groups.invite import (
    GROUP_INVITE_PREFIX,
    build_group_invite,
    decode_group_invite,
    encode_group_invite,
    looks_like_group_invite,
)
from i2pchat.presentation.group_conversations import render_group_control_text

ALICE = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BOB = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
CAROL = "cccccccccccccccccccccccccccccccccccccccc"


def test_group_invite_round_trip() -> None:
    invite = build_group_invite(
        group_id="group-abc",
        members=(ALICE, BOB),
        epoch=3,
        inviter_id=ALICE,
        title="Weekend",
        invite_id="deadbeef",
    )
    encoded = encode_group_invite(invite)
    assert encoded.startswith(GROUP_INVITE_PREFIX)
    assert looks_like_group_invite(encoded) is True

    decoded = decode_group_invite(encoded)
    assert decoded.group_id == "group-abc"
    assert decoded.title == "Weekend"
    assert decoded.epoch == 3
    assert decoded.inviter_id == ALICE
    assert decoded.members == (ALICE, BOB)
    assert decoded.invite_id == "deadbeef"


def test_decode_group_invite_rejects_malformed() -> None:
    with pytest.raises(ValueError, match="Not a group invite"):
        decode_group_invite("__I2PCHAT_GROUP__:{}")
    with pytest.raises(ValueError, match="Empty group invite"):
        decode_group_invite(GROUP_INVITE_PREFIX)
    with pytest.raises(ValueError, match="Invalid group invite JSON"):
        decode_group_invite(GROUP_INVITE_PREFIX + "{not-json")
    with pytest.raises(ValueError, match="group_id"):
        decode_group_invite(
            encode_group_invite(
                build_group_invite(
                    group_id="x",
                    members=(ALICE,),
                    epoch=0,
                    inviter_id=ALICE,
                )
            ).replace('"group_id":"x"', '"group_id":""')
        )


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
