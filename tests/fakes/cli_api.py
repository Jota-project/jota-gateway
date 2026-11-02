"""`Api` falso para los tests de la CLI: respuestas por (método, ruta) y registro de llamadas."""


class FakeApi:
    def __init__(self, responses=None, error=None):
        self.responses, self.error, self.calls = responses or {}, error, []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.error:
            raise self.error
        return self.responses[(method, path)]
