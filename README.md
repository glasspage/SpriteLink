SpriteLink is an end-to-end encrypted messaging program built around chatrooms.
It uses ntfy-compatible servers as encrypted message relays, allowing users to communicate securely without a dedicated backend.

SpriteLink is designed around the limitations of the public ntfy server, including its 4 KB message size limit, 250-message daily publish limit, and 12-hour server retention period. 
Ideally, *SpriteLink should be left running* so it can receive and save messages before they expire from the server.

DISCLAIMER: The code in this repository was created with assistance from AI tools. I made the architecture, design and implementation decisions; AI was used to write the code based on them.
