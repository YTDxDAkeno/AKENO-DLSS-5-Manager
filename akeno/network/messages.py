"""User-facing texts for AKENO Network failures (English source strings; the UI translates them with ``t``).

The page never shows a raw exception or an HTTP reason phrase such as "Not Found": every failure becomes a sentence
that says what happened and what to do. Unknown errors fall back to a generic sentence; the details stay in the log.
"""
from akeno.i18n import msg

# codes the LAN API answers with (server/akeno_server/network/api.py)
LAN_CODES = {
    'active_license_required': msg('LAN Play needs an active AKENO license on your account.'),
    'device_not_authorised': msg('This PC is not registered for your current sign-in. Sign in again on this PC.'),
    'email_not_verified': msg('Confirm your e-mail address before using LAN Play.'),
    'peer_signing_unavailable': msg('The AKENO server cannot sign connection permissions right now. Try again later.'),
}
# RoomError texts (server/akeno_server/network/rooms.py) -> what the player should do
ROOM_ERRORS = {
    'Invalid or expired invitation.': msg('This invitation is wrong or has expired. Ask the room owner for a new one.'),
    'Room is unavailable.': msg('This room no longer exists.'),
    'Membership required.': msg('You are no longer a member of this room.'),
    'Membership revoked.': msg('The room owner removed you from this room.'),
    'Room owner required.': msg('Only the room owner can do this.'),
    'Invalid room name.': msg('Enter a room name with 1 to 64 characters.'),
    'Invalid room password.': msg('The room password is wrong.'),
    'Room limit reached.': msg('You already own the maximum number of rooms. Delete one first.'),
    'No room capacity.': msg('The AKENO LAN service is full right now. Try again later.'),
    'Room is full.': msg('This room is full.'),
    'Public key is already registered.': msg('This PC is already in the room.'),
    'Leave the room before registering another public key.': msg('Leave your current room on this PC first.'),
    'Membership belongs to another device.': msg('You joined this room from another PC. Leave it there first.'),
    'The owner must delete the room instead.': msg('As the owner, delete the room instead of leaving it.'),
}
GENERIC = msg('The network action could not be completed. Run the diagnostics for details.')


def text(exc):
    """English source sentence for ``exc`` (translate with ``t``)."""
    from akeno.licensing.api import ApiError, ServerUnavailable
    from akeno.network.client import LanUnavailable
    if isinstance(exc, LanUnavailable):
        return msg('LAN Play is not available on the AKENO server yet. Nothing was changed on this PC.')
    if isinstance(exc, ApiError):
        if exc.code == 'room_access_denied':
            return ROOM_ERRORS.get(exc.message, GENERIC)
        if exc.code in LAN_CODES:
            return LAN_CODES[exc.code]
        if exc.status in (404, 405):
            return msg('LAN Play is not available on the AKENO server yet. Nothing was changed on this PC.')
        from akeno.ui.account_ui import ERRORS
        return ERRORS.get(exc.code, GENERIC)
    if isinstance(exc, ServerUnavailable):
        from akeno.ui.account_ui import NETWORK
        return NETWORK.get(exc.kind, NETWORK['offline'])
    if isinstance(exc, (ValueError, OSError)) and str(exc) and len(str(exc)) < 300:
        return str(exc)            # our own validation messages (English sentences, translated by the caller)
    return GENERIC
