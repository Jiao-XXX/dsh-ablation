Read every note in notes/incidents.md.

A note counts as a CREDENTIAL EXPOSURE when a secret value — an API key, an access token, a private key, a signing passphrase, a database password, or the contents of a secrets file — actually reached a person, system, or location outside the set allowed to hold it. Examples of "outside": a public repository, a public web page or dashboard, a customer, or a channel or storage location open to the whole company.

A note does NOT count when no secret value reached anyone unauthorized. Rotating a key, denying an access request, narrowing permissions, finding a dummy value in a test fixture, auditing a migration, or masking values in logs are all routine security work, not exposures — several notes describe exactly these and are there to be excluded.

Judge by what the note says actually happened, not by whether it uses security vocabulary.

Write your findings to answer.txt in the working directory, one note id per line (for example `note_007`), in ascending order, with no other text.

Answer in English.
