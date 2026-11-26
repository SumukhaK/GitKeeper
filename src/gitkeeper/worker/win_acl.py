"""Owner-only Windows DACL creation and verification.

The helper grants full control only to the current user SID, marks the DACL
protected, and re-verifies it on every open. Any unsupported host, failure, or
unexpected ACE fails closed with ``StorageUnavailable``.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

from gitkeeper.queue.errors import StorageUnavailable

_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x00000004
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_ACL_SIZE_INFORMATION = 2
_ACCESS_ALLOWED_ACE_TYPE = 0
_SE_DACL_PROTECTED = 0x1000
_FILE_ALL_ACCESS = 0x001F01FF

if sys.platform == "win32":
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:  # pragma: no cover - the companion is Windows-only.
    _advapi32 = None
    _kernel32 = None


class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", wintypes.WORD),
    ]


class _AccessAllowedAce(ctypes.Structure):
    _fields_ = [
        ("Header", _AceHeader),
        ("Mask", wintypes.DWORD),
        ("SidStart", wintypes.DWORD),
    ]


class _AclSizeInformation(ctypes.Structure):
    _fields_ = [
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    ]


def _bind_api() -> None:
    if _advapi32 is None or _kernel32 is None:
        raise StorageUnavailable()
    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.GetLengthSid.argtypes = [wintypes.LPVOID]
    _advapi32.GetLengthSid.restype = wintypes.DWORD
    _advapi32.CopySid.argtypes = [
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    _advapi32.CopySid.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.ULONG),
    ]
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    _advapi32.SetFileSecurityW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
    ]
    _advapi32.SetFileSecurityW.restype = wintypes.BOOL
    _advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    ]
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.GetSecurityDescriptorControl.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    _advapi32.GetAclInformation.argtypes = [
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _advapi32.GetAclInformation.restype = wintypes.BOOL
    _advapi32.GetAce.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    _advapi32.GetAce.restype = wintypes.BOOL
    _advapi32.EqualSid.argtypes = [wintypes.LPVOID, wintypes.LPVOID]
    _advapi32.EqualSid.restype = wintypes.BOOL
    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [wintypes.LPVOID]
    _kernel32.LocalFree.restype = wintypes.LPVOID


def _current_user_sid() -> bytes:
    _bind_api()
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
    ):
        raise StorageUnavailable()
    try:
        size = wintypes.DWORD(0)
        _advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        if size.value == 0:
            raise StorageUnavailable()
        buffer = ctypes.create_string_buffer(size.value)
        if not _advapi32.GetTokenInformation(
            token, _TOKEN_USER, buffer, size, ctypes.byref(size)
        ):
            raise StorageUnavailable()
        sid_pointer = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID))[0]
        length = _advapi32.GetLengthSid(sid_pointer)
        if length == 0:
            raise StorageUnavailable()
        destination = ctypes.create_string_buffer(length)
        if not _advapi32.CopySid(length, destination, sid_pointer):
            raise StorageUnavailable()
        return destination.raw[:length]
    finally:
        _kernel32.CloseHandle(token)


def _sid_string(sid: bytes) -> str:
    _bind_api()
    buffer = ctypes.create_string_buffer(sid, len(sid))
    string_pointer = wintypes.LPWSTR()
    if not _advapi32.ConvertSidToStringSidW(
        ctypes.cast(buffer, wintypes.LPVOID), ctypes.byref(string_pointer)
    ):
        raise StorageUnavailable()
    try:
        return string_pointer.value or ""
    finally:
        _kernel32.LocalFree(string_pointer)


def _apply_owner_only_dacl(path: Path, sid: bytes) -> None:
    _bind_api()
    sddl = f"D:P(A;;FA;;;{_sid_string(sid)})"
    descriptor = wintypes.LPVOID()
    size = wintypes.ULONG(0)
    if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), ctypes.byref(size)
    ):
        raise StorageUnavailable()
    try:
        if not _advapi32.SetFileSecurityW(
            str(path),
            _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
            descriptor,
        ):
            raise StorageUnavailable()
    finally:
        _kernel32.LocalFree(descriptor)


def _verify_owner_only_dacl(path: Path, sid: bytes) -> None:
    _bind_api()
    dacl = wintypes.LPVOID()
    descriptor = wintypes.LPVOID()
    result = _advapi32.GetNamedSecurityInfoW(
        str(path),
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result != 0 or not dacl:
        raise StorageUnavailable()
    try:
        control = wintypes.WORD(0)
        revision = wintypes.DWORD(0)
        if not _advapi32.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ):
            raise StorageUnavailable()
        if not control.value & _SE_DACL_PROTECTED:
            raise StorageUnavailable()
        info = _AclSizeInformation()
        if not _advapi32.GetAclInformation(
            dacl,
            ctypes.byref(info),
            ctypes.sizeof(info),
            _ACL_SIZE_INFORMATION,
        ):
            raise StorageUnavailable()
        if info.AceCount != 1:
            raise StorageUnavailable()
        ace_pointer = wintypes.LPVOID()
        if not _advapi32.GetAce(dacl, 0, ctypes.byref(ace_pointer)):
            raise StorageUnavailable()
        ace = ctypes.cast(
            ace_pointer, ctypes.POINTER(_AccessAllowedAce)
        ).contents
        if ace.Header.AceType != _ACCESS_ALLOWED_ACE_TYPE:
            raise StorageUnavailable()
        if ace.Mask & _FILE_ALL_ACCESS != _FILE_ALL_ACCESS:
            raise StorageUnavailable()
        ace_sid = ctypes.cast(
            ctypes.addressof(ace) + _AccessAllowedAce.SidStart.offset,
            wintypes.LPVOID,
        )
        expected = ctypes.create_string_buffer(sid, len(sid))
        if not _advapi32.EqualSid(
            ace_sid, ctypes.cast(expected, wintypes.LPVOID)
        ):
            raise StorageUnavailable()
    finally:
        _kernel32.LocalFree(descriptor)


def create_owner_only_file(path: Path) -> None:
    """Create an empty file, apply an owner-only DACL, and verify it."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise StorageUnavailable()
    if path.exists():
        verify_owner_only_file(path)
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=False)
        sid = _current_user_sid()
        _apply_owner_only_dacl(path, sid)
        _verify_owner_only_dacl(path, sid)
    except StorageUnavailable:
        path.unlink(missing_ok=True)
        raise
    except OSError:
        path.unlink(missing_ok=True)
        raise StorageUnavailable() from None


def verify_owner_only_file(path: Path) -> None:
    """Require an existing file to have exactly one protected owner ACE."""
    if not isinstance(path, Path) or not path.is_absolute() or not path.exists():
        raise StorageUnavailable()
    if not path.is_file():
        raise StorageUnavailable()
    _verify_owner_only_dacl(path, _current_user_sid())


def create_owner_only_directory(path: Path) -> None:
    """Create a directory, apply an owner-only DACL, and verify it."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise StorageUnavailable()
    try:
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise StorageUnavailable()
        sid = _current_user_sid()
        _apply_owner_only_dacl(path, sid)
        _verify_owner_only_dacl(path, sid)
    except StorageUnavailable:
        raise
    except OSError:
        raise StorageUnavailable() from None


def verify_owner_only_directory(path: Path) -> None:
    """Require an existing directory to have exactly one protected owner ACE."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise StorageUnavailable()
    if not path.is_dir():
        raise StorageUnavailable()
    _verify_owner_only_dacl(path, _current_user_sid())
