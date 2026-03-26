#!/usr/bin/env python3
import http.server
import socketserver
import os

PORT = 8080
os.chdir('/home/user/chatbot-piscines')

Handler = http.server.SimpleHTTPRequestHandler

print("🚀 Chatbot Server démarré!")
print(f"📍 URL: http://localhost:{PORT}/index.html")
print("✅ Le chatbot est accessible!")

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    httpd.serve_forever()
