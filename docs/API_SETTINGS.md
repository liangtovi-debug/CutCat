# Simple custom model API settings (configuration preparation only)

The AI panel's **Custom model API** button opens a bilingual, three-step dialog:
1. Add a provider: optional name, HTTPS API base address, and optional key.
2. Choose a model: enter the exact model ID from that provider.
3. Start chatting: visibly unavailable until a real transport is implemented.

This follows the supplied visual reference's numbered, plain-language guidance.
**Check fields** checks local format only. **Test connection** is disabled and its
reason is shown. **Apply for this session** keeps the entered values only in this
running application and does not switch the existing offline editor to a remote
provider. The dialog does not fetch models, call an API, validate the key, spend
money, or send captions/media. A provider may require `/v1`; the app does not guess
or append a version. Operation URLs such as `/chat/completions` are rejected in
favor of the provider's API base address.

## Privacy and state

The key is optional for preparation, masked by default, and reveal is explicit.
No key is prefilled from a conversation or environment variable. Neither the key
nor other API settings are written to disk, project files, a registry, or logs.
Session representation and errors omit secrets. Input validation rejects control
characters; URL validation requires HTTPS and rejects userinfo, query/fragment,
escaped separators, traversal and obvious operation endpoints. No connection is
made even for private/local hostnames. These checks are not a claim that a domain
is trustworthy or reachable, and do not substitute for future outbound consent.

Cancel/close leaves the existing session unchanged. Unchanged Apply preserves its
identity. Changed Apply creates a new immutable session and invalidates old
editing previews; no project or undo mutation occurs. Applying settings is not
approval to send them to a provider or apply an edit. The controlled proposal
preview always requires its own exact local confirmation.

This is Python process-memory retention, not a secure enclave or a guarantee of
memory zeroization. Persistent secure credential storage and any real remote
adapter are out of scope. The existing optional SDK adapter remains explicitly
local-fake-only, and default editing still works without that SDK installed.

Validation uses synthetic `.invalid` URLs and synthetic keys only. Linux offscreen
Qt screenshots and tests do not certify Windows/macOS native behavior.
