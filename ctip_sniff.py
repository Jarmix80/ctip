import socket, time, pathlib, datetime as dt

HOST, PORT, PIN = "192.168.0.11", 5524, "1234"
OUT = pathlib.Path("ctip_sniff.log")

def w(s): OUT.open("a", encoding="utf-8").write(s + "\n")

with socket.create_connection((HOST, PORT), timeout=5) as s:
    s.settimeout(30)
    s.sendall(b"aWHO\r\n"); time.sleep(0.2)
    s.sendall(("aLOGA " + PIN + "\r\n").encode("ascii", "ignore"))

    buf = b""
    start = time.time()
    while time.time() - start < 30:  # 30 sekund podsłuchu
        chunk = s.recv(4096)
        if not chunk: break
        buf += chunk
        while True:
            i = buf.find(b"\r\n")
            if i < 0: break
            line = buf[:i]; buf = buf[i+2:]
            # zapisujemy surowe bajty jako repr (bez dekodowania)
            w(f"{dt.datetime.now().isoformat()} << {repr(line)}")
