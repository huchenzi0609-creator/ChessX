"""Lossless JSON changes for journal storage, independent of game semantics."""

from copy import deepcopy


def state_delta(before: dict, after: dict) -> dict:
    changes = {"set": [], "delete": [], "list": []}

    def visit(old, new, path):
        if old == new:
            return
        if isinstance(old, dict) and isinstance(new, dict):
            for key in old.keys() - new.keys():
                changes["delete"].append([*path, key])
            for key, value in new.items():
                if key not in old:
                    changes["set"].append([[*path, key], deepcopy(value)])
                else:
                    visit(old[key], value, [*path, key])
        elif isinstance(old, list) and isinstance(new, list):
            prefix = 0
            while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
                prefix += 1
            changes["list"].append([path, prefix, deepcopy(new[prefix:])])
        else:
            changes["set"].append([path, deepcopy(new)])

    visit(before, after, [])
    return changes


def apply_delta(before: dict, delta: dict) -> dict:
    state = deepcopy(before)

    def parent(path):
        current = state
        for key in path[:-1]:
            current = current[key]
        return current

    for path in delta["delete"]:
        del parent(path)[path[-1]]
    for path, value in delta["set"]:
        if not path:
            state = deepcopy(value)
        else:
            parent(path)[path[-1]] = deepcopy(value)
    for path, start, tail in delta["list"]:
        target = state
        for key in path:
            target = target[key]
        target[start:] = deepcopy(tail)
    return state
