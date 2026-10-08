SpriteLink is an end-to-end encrypted messaging program built around chatrooms.
It uses ntfy-compatible servers as encrypted message relays, allowing users to communicate securely without a dedicated backend.

SpriteLink is designed around the limitations of the public ntfy server, including its 4 KB message size limit, 250-message daily publish limit, and 12-hour server retention period. 
Ideally, *SpriteLink should be left running* so it can receive and save messages before they expire from the server.

The Glassy theme uses native Windows blur behind the translucent window, with an opaque fallback where native blur is unavailable. Saved Glassy+ settings migrate to Glassy.

The Global view shows a "Recently online" count beside Config. It estimates participating clients from encrypted anonymous pings in the last six hours. Each client publishes at most one new ping every six hours, including while viewing another chatroom, without publishing usernames or chat identities. The count refreshes every five minutes unless the program is minimized to tray, and refreshes immediately when restored. The last completed count stays displayed until a new tally arrives.

DISCLAIMER: The code in this repository was created with assistance from AI tools. I made the architecture, design and implementation decisions; AI was used to write the code based on them.
