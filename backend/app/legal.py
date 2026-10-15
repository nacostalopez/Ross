"""The version of the terms of service and privacy policy a person accepts.

Registration, accepting an invite and the landing's demo form record the version they were shown (and when),
so it is possible to tell later who accepted which text. Change it when the texts change in a way people
have to accept again.
"""

from fastapi import HTTPException, status

LEGAL_VERSION = "2026-10-15"

TERMS_NOT_ACCEPTED = "Tenés que aceptar los términos de servicio y la política de privacidad para crear la cuenta."
PRIVACY_NOT_ACCEPTED = "Tenés que aceptar la política de privacidad para enviar el pedido."


def require_acceptance(accepted: bool, message: str) -> None:
    """A plain 400 with a sentence the person can act on, not a list of field errors."""
    if accepted is not True:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
