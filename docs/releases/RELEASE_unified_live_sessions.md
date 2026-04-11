# Unified live sessions and ACK routing (internal)

## Summary

- **Transport truth** is per-peer: every live SAM stream is stored as **`LivePeerSession`** in **`I2PChatCore._live_sessions`**. Legacy **`self.conn`** / **`LegacyCoreSessionView`** are removed.
- **`current_peer_addr`** is documented as **UI selection** (active chat). It must not be used as the sole source for outbound ACK registration or writer routing; sends use the **routing peer** for that operation.
- **`_register_pending_ack(..., routing_peer_id=...)`** records **`PendingAckEntry.peer_addr`** and **`SessionManager.register_inflight_message`** from the **routing** peer, not from “current UI peer”.
- **Incoming connections** no longer flip **`current_peer_addr`** automatically; notifications and session lists still update; switching chat is explicit in the GUI.
- **`receive_loop`** correlates **`MSG_ACK` / `IMG_ACK` / `FILE_ACK`** with **`sess.peer_id`** and **`sess._ack_session_epoch`**, and **`acknowledge_inflight_message`** uses the same session peer.

See **`docs/ARCHITECTURE.md`** (component map) for the stable description.
