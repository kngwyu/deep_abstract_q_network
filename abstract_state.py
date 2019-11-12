class AbstractState(object):

    def get_key_lazy(self):
        raise NotImplemented

    def get_vector_lazy(self):
        raise NotImplemented

    def get_key(self):
        if hasattr(self, '__key'):
            return self.__key
        else:
            self.__key = self.get_key_lazy()
            return self.__key

    def get_vector(self):
        if hasattr(self, '__vector'):
            return self.__vector
        else:
            self.__vector = self.get_vector_lazy()
            return self.__vector

    def __hash__(self):
        return hash(self.get_key())

    def __eq__(self, other):
        if not issubclass(other.__class__, AbstractState):
            return False
        else:
            return self.get_key() == other.get_key()

    def __ne__(self, other):
        return not self.__eq__(other)

    def __str__(self):
        return str(self.get_key())
