// Scripture Browser — tells Firefox to read scripture-browser.cfg from its own
// installation directory, before any profile is opened.
//
// general.config.obscure_value = 0 means the .cfg beside it is plain text, not
// byte-shifted.  install.sh copies both of these into the Firefox directory and
// records them, so a missing one is put back.
pref("general.config.filename", "scripture-browser.cfg");
pref("general.config.obscure_value", 0);
