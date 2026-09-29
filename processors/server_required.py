# -*- coding: utf-8 -*-
"""The ONE refusal every claims processor raises when it has no server.

Claim geometry, numbering, corner alignment and validation all run on
geodb.io. The plugin keeps no local copy of any of them (grid generation's
was deleted 2026-09-14; ordering, corner alignment and validation followed
2026-09-29 in the qgis-thin-client build). Two reasons, and both matter:

1. **One answer.** A local copy drifts from the server's rule in a single
   commit, and every fallback here fired on ANY server error, not only
   offline, so a transient 500 quietly produced a different answer.
2. **The plugin is public.** What lives in this repository is readable by
   anyone; the algorithms are the server's.

A processor built without an ``api_client`` used to fall back silently. That
hid a wiring bug for two weeks: the wizard's Layout step never passed its
client, and the fallback drew claims locally until the fallback was deleted.
The refusal is loud so the next such bug is found at once.
"""


def require_server(api_client, what: str) -> None:
    """Raise the readable refusal when ``api_client`` is missing.

    Args:
        api_client: the processor's APIClient, or None.
        what: the action, as a sentence subject ("Numbering claims").
    """
    if api_client is not None:
        return
    raise RuntimeError(
        f"{what} runs on the geodb.io server and needs a signed-in "
        f"connection. Claim geometry, numbering and checks all come from "
        f"one place so that every claim (here, on the web map, and in the "
        f"filed documents) agrees. Sign in on the Account tab and try again."
    )
