"""HTTP routers for Dashboard API resources.

Keep routers thin: request parsing, auth dependencies, status codes, and
response shaping. Business logic belongs in services; SQL belongs in
repositories.
"""

