#!/bin/bash
set -e
cd "$(dirname "$0")"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Facebook Ads Viewer — Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

echo "📦 Installation des dépendances Python..."
pip3 install -r requirements.txt --break-system-packages -q

echo "🌐 Installation du navigateur Chromium pour Playwright..."
python3 -m playwright install chromium

echo ""
echo "✅ Setup terminé !"
echo ""
echo "🚀 Lancement sur http://localhost:5000"
echo "   (Ctrl+C pour arrêter)"
echo ""

python3 app.py
