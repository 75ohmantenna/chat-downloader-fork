# SPDX-License-Identifier: MIT

"""Shared remapping utilities."""

from collections.abc import Callable, Mapping
from typing import Any


class Remapper:
    """Control the remapping of one dictionary to another dictionary."""

    def __init__(
        self,
        new_key: str | None = None,
        remap_function: Callable[[Any], Any] | None = None,
        to_unpack: bool = False,
    ) -> None:
        """Configure how one source key is transformed during remapping."""
        if new_key is not None and to_unpack:
            msg = "If to_unpack is True, new_key may not be specified."
            raise ValueError(msg)

        self.new_key = new_key

        if isinstance(remap_function, staticmethod):
            remap_function = remap_function.__func__

        self.remap_function = remap_function
        self.to_unpack = to_unpack

    @staticmethod
    def remap(
        info: dict[str, Any],
        remapping_dict: Mapping[str, Any],
        remap_key: str,
        remap_input: object,
        keep_unknown_keys: bool = False,
        replace_char_with_underscores: str | None = None,
    ) -> None:
        """Remap one input item into the destination dictionary."""
        remap = remapping_dict.get(remap_key)

        if remap:
            if isinstance(remap, Remapper):
                new_key = remap.new_key
                if remap.remap_function is not None:
                    new_value = remap.remap_function(remap_input)
                else:
                    new_value = remap_input

                if not remap.to_unpack:
                    if new_key is not None:
                        info[new_key] = new_value
                elif isinstance(new_value, dict):
                    info.update(new_value)
                else:
                    msg = "Unable to unpack item which is not a dictionary."
                    raise ValueError(msg)

            elif isinstance(remap, str):
                info[remap] = remap_input
            else:
                msg = "Unknown remapping specified."
                raise ValueError(msg)

        elif keep_unknown_keys:
            if replace_char_with_underscores:
                remap_key = remap_key.replace(
                    replace_char_with_underscores, "_"
                )
            info[remap_key] = remap_input

    @staticmethod
    def remap_dict(
        input_dictionary: dict[str, Any],
        remapping_dict: Mapping[str, Any],
        keep_unknown_keys: bool = False,
        replace_char_with_underscores: str | None = None,
    ) -> dict[str, Any]:
        """Return a remapped dictionary."""
        info: dict[str, Any] = {}
        for key, value in input_dictionary.items():
            Remapper.remap(
                info,
                remapping_dict,
                key,
                value,
                keep_unknown_keys=keep_unknown_keys,
                replace_char_with_underscores=replace_char_with_underscores,
            )
        return info
