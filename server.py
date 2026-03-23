#!/usr/bin/env python3
import http.server
import socketserver
import os

PORT = 8080
os.chdir('/home/user/chatbot-piscines')

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()
    
    def log_message(self, format, *args):
        print(f"[SERVER] {self.address_string()} - {format % args}")

print(f"🚀 Chatbot Server démarré!")
print(f"📍 URL locale: http://localhost:{PORT}")
print(f"📍 URL réseau: http://$(hostname -I | awk '{print $1}'):{PORT}")
print(f"📄 Chatbot: http://localhost:{PORT}/index.html")
print(f"\n✅ Le chatbot est maintenant accessible!")

with socketserver.TCPServer(("", PORT), MyHTTPRequestHandler) as httpd:
    httpd.serve_forever()
