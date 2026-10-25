"""Offline bootstrap for a server administrator; never exposed as a web route."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from database.session import init_db
from services.web_auth import create_account

if __name__=="__main__":
    parser=argparse.ArgumentParser(description="Create a local website administrator and display the generated password once.")
    parser.add_argument("--login",required=True)
    args=parser.parse_args()
    init_db()
    result=create_account(None,args.login,admin=True,bootstrap=True)
    print("Identifiant :",result["credentials"]["login"])
    print("Mot de passe généré (à conserver maintenant) :",result["credentials"]["password"])
