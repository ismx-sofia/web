#!/usr/bin/env bash
# Checks the live site after a Pages deployment. Usage: tools/check_live.sh [https://www.sofia.ismx.app]
# Prints one line per URL and exits non-zero if any status differs from the expected one.
set -u
H="${1:-https://www.sofia.ismx.app}"
fail=0
check() { # url expected-status [must-contain]
  local code body
  body=$(curl -s -L --max-redirs 0 -o /tmp/sofia_live.$$ -w '%{http_code}' "$H$1")
  code=$body
  if [ "$code" != "$2" ]; then echo "FAIL $1 → $code (expected $2)"; fail=1; return; fi
  if [ -n "${3:-}" ] && ! grep -q -- "$3" /tmp/sofia_live.$$; then echo "FAIL $1 → missing '$3'"; fail=1; return; fi
  if grep -q 'class="pending"' /tmp/sofia_live.$$; then echo "FAIL $1 → draft build published (pending fields)"; fail=1; return; fi
  echo "ok   $1 → $code"
}
check /                    200 'Dale voz a tu'
check /en/                 200 'Your story'
check /en                  301
check /pt/                 200 'hreflang="pt"'
check /faq                 200 'id="seguridad"'
check /help                200 'hello@sofia.ismx.app'
check /delete-account      200 'PROGRAMSAN'
check /privacy             200
check /terms               200
check /ia                  200
check /legal               200 'B40623829'
check /normas              200
check /cookies             200
check /entry/abc?share=x   404 'id="shared"'
check /otp-test.html       404
check /src/i18n/es.json    404
check /tools/build.py      404
check /.well-known/apple-app-site-association 200 'applinks'
check /.well-known/assetlinks.json            200 'com.ismx.sofia'
check /app-ads.txt         200 'pub-4226754379627519'
check /sitemap.xml         200 'xhtml:link'
check /robots.txt          200 'Sitemap:'
# AdMob drops the www. when it crawls app-ads.txt (audit R11): the bare domain has to answer.
apex=$(curl -s -o /dev/null -w '%{http_code}' -L "https://sofia.ismx.app/app-ads.txt")
if [ "$apex" = "200" ]; then echo "ok   https://sofia.ismx.app/app-ads.txt → 200"; else echo "FAIL https://sofia.ismx.app/app-ads.txt → $apex (DNS of the bare domain)"; fail=1; fi
rm -f /tmp/sofia_live.$$
exit $fail
