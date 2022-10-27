from numbers import Integral, Number

import numpy as np

from ..dtypes import _INDEX, lookup_dtype
from . import ffi, lib


def libget(name):
    """Helper to get items from GraphBLAS which might be GrB or GxB"""
    try:
        return getattr(lib, name)
    except AttributeError:
        ext_name = f"GxB_{name[4:]}"
        try:
            return getattr(lib, ext_name)
        except AttributeError:
            pass
        raise


def wrapdoc(func_with_doc):
    """Decorator to copy `__doc__` from a function onto the wrapped function"""

    def inner(func_wo_doc):
        func_wo_doc.__doc__ = func_with_doc.__doc__
        return func_wo_doc

    return inner


# Include most common types (even mistakes)
_output_types = {
    int: int,
    float: float,
    list: list,
    slice: slice,
    tuple: tuple,
    np.ndarray: np.ndarray,
    # Mistakes
    object: object,
    type: type,
}
_output_types.update((k, k) for k in np.cast)


def output_type(val):
    try:
        return _output_types[type(val)]
    except KeyError:
        return type(val)


def ints_to_numpy_buffer(array, dtype, *, name="array", copy=False, ownable=False, order="C"):
    if (
        isinstance(array, np.ndarray)
        and not np.issubdtype(array.dtype, np.integer)
        and not np.issubdtype(array.dtype, np.bool8)
    ):
        raise ValueError(f"{name} must be integers, not {array.dtype.name}")
    array = np.array(array, dtype, copy=copy, order=order)
    if ownable and (not array.flags.owndata or not array.flags.writeable):
        array = array.copy(order)
    return array


def _get_subdtype(dtype):
    while dtype.subdtype is not None:
        dtype = dtype.subdtype[0]
    return dtype


def values_to_numpy_buffer(array, dtype=None, *, copy=False, ownable=False, order="C"):
    if dtype is not None:
        dtype = lookup_dtype(dtype)
        array = np.array(array, _get_subdtype(dtype.np_type), copy=copy, order=order)
    else:
        is_input_np = isinstance(array, np.ndarray)
        array = np.array(array, copy=copy, order=order)
        if array.dtype.hasobject:
            raise ValueError("object dtype for values is not allowed")
        if not is_input_np and array.dtype == np.int32:  # pragma: no cover
            # fix for win64 numpy handling of ints
            array = array.astype(np.int64)
        dtype = lookup_dtype(array.dtype)
    if ownable and (not array.flags.owndata or not array.flags.writeable):
        array = array.copy(order)
    return array, dtype


def get_shape(nrows, ncols, dtype=None, **arrays):
    if nrows is None or ncols is None:
        # Get nrows and ncols from the first 2d array
        is_subarray = dtype.np_type.subdtype is not None
        for name, arr in arrays.items():
            if name == "values" and is_subarray:
                # We could be smarter and determine the shape of the dtype sub-arrays
                if arr.ndim >= 3:
                    break
            elif arr.ndim == 2:
                break
        else:
            raise ValueError(
                "Either nrows and ncols must be provided, or one of the following arrays"
                f'must be 2d (from which to get nrows and ncols): {", ".join(arrays)}'
            )
        if nrows is None:
            nrows = arr.shape[0]
        if ncols is None:
            ncols = arr.shape[1]
    return nrows, ncols


def get_order(order):
    val = order.lower()
    if val in {"c", "row", "rows", "rowwise"}:
        return "rowwise"
    elif val in {"f", "col", "cols", "column", "columns", "colwise", "columnwise"}:
        return "columnwise"
    else:
        raise ValueError(
            f"Bad value for order: {order!r}.  "
            'Expected "rowwise", "columnwise", "rows", "columns", "C", or "F"'
        )


def normalize_chunks(chunks, shape):
    """Normalize chunks argument for use by `Matrix.ss.split`.

    Examples
    --------
    >>> shape = (10, 20)
    >>> normalize_chunks(10, shape)
    [(10,), (10, 10)]
    >>> normalize_chunks((10, 10), shape)
    [(10,), (10, 10)]
    >>> normalize_chunks([None, (5, 15)], shape)
    [(10,), (5, 15)]
    >>> normalize_chunks((5, (5, None)), shape)
    [(5, 5), (5, 15)]
    """
    if isinstance(chunks, (list, tuple)):
        pass
    elif isinstance(chunks, Number):
        chunks = (chunks,) * len(shape)
    elif isinstance(chunks, np.ndarray):
        chunks = chunks.tolist()
    else:
        raise TypeError(
            f"chunks argument must be a list, tuple, or numpy array; got: {type(chunks)}"
        )
    if len(chunks) != len(shape):
        typ = "Vector" if len(shape) == 1 else "Matrix"
        raise ValueError(
            f"chunks argument must be of length {len(shape)} (one for each dimension of a {typ})"
        )
    chunksizes = []
    for size, chunk in zip(shape, chunks):
        if chunk is None:
            cur_chunks = [size]
        elif isinstance(chunk, Integral) or isinstance(chunk, float) and chunk.is_integer():
            chunk = int(chunk)
            if chunk < 0:
                raise ValueError(f"Chunksize must be greater than 0; got: {chunk}")
            div, mod = divmod(size, chunk)
            cur_chunks = [chunk] * div
            if mod:
                cur_chunks.append(mod)
        elif isinstance(chunk, (list, tuple)):
            cur_chunks = []
            none_index = None
            for c in chunk:
                if isinstance(c, Integral) or isinstance(c, float) and c.is_integer():
                    c = int(c)
                    if c < 0:
                        raise ValueError(f"Chunksize must be greater than 0; got: {c}")
                elif c is None:
                    if none_index is not None:
                        raise TypeError(
                            'None value in chunks for "the rest" can only appear once per dimension'
                        )
                    none_index = len(cur_chunks)
                    c = 0
                else:
                    raise TypeError(
                        "Bad type for element in chunks; expected int or None, but got: "
                        f"{type(chunks)}"
                    )
                cur_chunks.append(c)
            if none_index is not None:
                fill = size - sum(cur_chunks)
                if fill < 0:
                    raise ValueError(
                        "Chunks are too large; None value in chunks would need to be negative "
                        "to match size of input"
                    )
                cur_chunks[none_index] = fill
        elif isinstance(chunk, np.ndarray):
            if not np.issubdtype(chunk.dtype, np.integer):
                raise TypeError(f"numpy array for chunks must be integer dtype; got {chunk.dtype}")
            if chunk.ndim != 1:
                raise TypeError(
                    f"numpy array for chunks must be 1-dimension; got ndim={chunk.ndim}"
                )
            if (chunk < 0).any():
                raise ValueError(f"Chunksize must be greater than 0; got: {chunk[chunk < 0]}")
            cur_chunks = chunk.tolist()
        else:
            raise TypeError(
                "Chunks for a dimension must be an integer, a list or tuple of integers, or None."
                f"  Got: {type(chunk)}"
            )
        chunksizes.append(cur_chunks)
    return chunksizes


class class_property:
    __slots__ = "classval", "member_property"

    def __init__(self, member_property, classval):
        self.classval = classval
        self.member_property = member_property

    def __get__(self, obj, type=None):
        if obj is None:
            return self.classval
        return self.member_property.__get__(obj, type)

    @property
    def __set__(self):
        return self.member_property.__set__


# A similar object may eventually make it to the GraphBLAS spec.
# Hide this from the user for now.
class _CArray:
    __slots__ = "array", "_carg", "dtype", "_name"

    def __init__(self, array=None, dtype=_INDEX, *, size=None, name=None):
        if size is not None:
            self.array = np.empty(size, dtype=dtype.np_type)
        else:
            self.array = np.array(array, dtype=_get_subdtype(dtype.np_type), copy=False, order="C")
        c_type = dtype.c_type if dtype._is_udt else f"{dtype.c_type}*"
        self._carg = ffi.cast(c_type, ffi.from_buffer(self.array))
        self.dtype = dtype
        self._name = name

    @property
    def name(self):
        if self._name is not None:
            return self._name
        if len(self.array) < 20:
            values = ", ".join(map(str, self.array))
        else:
            values = (
                f"{', '.join(map(str, self.array[:5]))}, "
                "..., "
                f"{', '.join(map(str, self.array[-5:]))}"
            )
        return f"({self.dtype.c_type}[]){{{values}}}"


class _Pointer:
    __slots__ = "val"

    def __init__(self, val):
        self.val = val

    @property
    def _carg(self):
        return self.val.gb_obj

    @property
    def name(self):
        name = self.val.name
        if not name:
            c = type(self.val).__name__[0]
            name = f"{'M' if c == 'M' else c.lower()}_temp"
        return f"&{name}"


class _MatrixArray:
    __slots__ = "_carg", "_exc_arg", "name"

    def __init__(self, matrices, exc_arg=None, *, name):
        from .base import record_raw

        self._carg = matrices
        self._exc_arg = exc_arg
        self.name = name
        record_raw(f"GrB_Matrix {name}[{len(matrices)}];")


def _autogenerate_code(
    filename,
    text,
    specializer=None,
    begin="# Begin auto-generated code",
    end="# End auto-generated code",
):
    """Super low-tech auto-code generation used by automethods.py and infixmethods.py"""
    with open(filename) as f:
        orig_text = f.read()
    if specializer:
        begin = f"{begin}: {specializer}"
        end = f"{end}: {specializer}"
    begin += "\n"
    end += "\n"

    boundaries = []
    stop = 0
    while True:
        try:
            start = orig_text.index(begin, stop)
            stop = orig_text.index(end, start)
        except ValueError:
            break
        boundaries.append((start, stop))
    new_text = orig_text
    for start, stop in reversed(boundaries):
        new_text = f"{new_text[:start]}{begin}{text}{new_text[stop:]}"
    with open(filename, "w") as f:
        f.write(new_text)
    import subprocess

    subprocess.check_call(["black", filename])