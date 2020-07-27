class Mask:
    complement = False
    structure = False
    value = False

    def __init__(self, mask):
        self.mask = mask

    def __repr__(self):
        return self.mask.__repr__(mask=self)

    def _repr_html_(self):
        return self.mask._repr_html_(mask=self)


class StructuralMask(Mask):
    complement = False
    structure = True
    value = False

    def __invert__(self):
        return ComplementedStructuralMask(self.mask)


class ValueMask(Mask):
    complement = False
    structure = False
    value = True

    def __invert__(self):
        return ComplementedValueMask(self.mask)


class ComplementedStructuralMask(Mask):
    complement = True
    structure = True
    value = False

    def __invert__(self):
        return StructuralMask(self.mask)


class ComplementedValueMask(Mask):
    complement = True
    structure = False
    value = True

    def __invert__(self):
        return ValueMask(self.mask)