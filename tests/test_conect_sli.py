import unittest

import conect_sli


class DummySendSocket:
    def __init__(self):
        self.data = []

    def sendall(self, payload: bytes) -> None:
        self.data.append(payload)


class DummyRecvSocket:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def recv(self, size: int) -> bytes:
        if self.chunks:
            return self.chunks.pop(0)
        return b""


class TimeoutThenDataSocket:
    def __init__(self):
        self.calls = 0

    def recv(self, size: int) -> bytes:
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError()
        if self.calls == 2:
            return b"aOK test\r\n"
        return b""


class ConectSliTests(unittest.TestCase):
    def test_send_command_prefix(self):
        sock = DummySendSocket()
        conect_sli.send_command(sock, "WHO")
        conect_sli.send_command(sock, "aPING")
        self.assertEqual(
            sock.data,
            [b"aWHO\r\n", b"aPING\r\n"],
        )

    def test_iter_lines_yields_complete_frames(self):
        sock = DummyRecvSocket([b"aOK centr", b"ala\r\nRING 201", b" 555\r\n", b""])
        gen = conect_sli.iter_lines(sock)
        self.assertEqual("aOK centrala", next(gen))
        self.assertEqual("RING 201 555", next(gen))
        with self.assertRaises(ConnectionError):
            next(gen)

    def test_iter_lines_handles_timeouts(self):
        sock = TimeoutThenDataSocket()
        gen = conect_sli.iter_lines(sock)
        self.assertEqual("aOK test", next(gen))
        with self.assertRaises(ConnectionError):
            next(gen)


if __name__ == "__main__":
    unittest.main()
