import logging
import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Callable, Optional, Union

# https://stackoverflow.com/a/14693789
ANSI_ESCAPE = re.compile(
    r"""
    \x1B  # ESC
    (?:   # 7-bit C1 Fe (except CSI)
        [@-Z\\-_]
    |     # or [ for CSI, followed by a control sequence
        \[
        [0-?]*  # Parameter bytes
        [ -/]*  # Intermediate bytes
        [@-~]   # Final byte
    )
""",
    re.VERBOSE,
)


def snippet_to_regex_fn(snippet: str) -> str:
    """Convert a snippet to a regex that matches the snippet, treating
    `...` as a wildcard.
    """
    return re.escape(snippet).replace(r"\.\.\.", ".*")


def re_ignore_before(match_str: str) -> tuple[str, str]:
    """Generates a regex substitution pair that replaces any text before `match_str` with
    an ellipses.
    """
    return (rf"[\s\S]*{re.escape(match_str)}", f"...\n{match_str}")


def re_ignore_after(match_str: str) -> tuple[str, str]:
    """Generates a regex substitution pair that replaces any text after `match_str` with
    an ellipses.
    """
    return (rf"{re.escape(match_str)}[\s\S]*", f"{match_str}\n...")


PWD_REGEX = re.compile(r"PWD=(.*?);")


def _run_command(cmd: Union[str, Sequence[str]], expect_error: bool = False) -> str:
    if not isinstance(cmd, str):
        cmd = " ".join(cmd)

    try:
        actual_output = (
            subprocess.check_output(
                f'{cmd} && echo "PWD=$(pwd);"', shell=True, stderr=subprocess.STDOUT
            )
            .decode("utf-8")
            .strip()
        )
        if expect_error:
            print(f"Ran command {cmd}")  # noqa: T201
            print("Got output:")  # noqa: T201
            print(actual_output)  # noqa: T201
            raise Exception("Expected command to fail")
    except subprocess.CalledProcessError as e:
        if expect_error:
            actual_output = e.output.decode("utf-8").strip()
        else:
            print(f"Ran command {cmd}")  # noqa: T201
            print("Got output:")  # noqa: T201
            print(e.output.decode("utf-8").strip())  # noqa: T201
            raise

    pwd = PWD_REGEX.search(actual_output)
    if pwd:
        actual_output = PWD_REGEX.sub("", actual_output)
        os.chdir(pwd.group(1))

    actual_output = ANSI_ESCAPE.sub("", actual_output)

    return actual_output


def _assert_matches_or_update_snippet(
    contents: str,
    snippet_path: Path,
    update_snippets: bool,
    snippet_replace_regex: Optional[Sequence[tuple[str, str]]],
    custom_comparison_fn: Optional[Callable[[str, str], bool]],
):
    comparison_fn = custom_comparison_fn or (
        lambda actual, expected: actual == expected
    )
    if snippet_replace_regex:
        for regex, replacement in snippet_replace_regex:
            contents = re.sub(regex, replacement, contents, re.MULTILINE | re.DOTALL)

    snippet_output_file = Path(snippet_path)
    snippet_output_file.parent.mkdir(parents=True, exist_ok=True)

    if update_snippets:
        snippet_output_file.write_text(f"{contents.rstrip()}\n")
        print(f"Updated snippet at {snippet_path}")  # noqa: T201
    else:
        if not snippet_output_file.exists():
            raise Exception(f"Snippet at {snippet_path} does not exist")

        contents = contents.rstrip()
        snippet_contents = snippet_output_file.read_text().rstrip()
        if not comparison_fn(contents, snippet_contents):
            print(f"Snapshot mismatch {snippet_path}")  # noqa: T201
            print("\nActual file:")  # noqa: T201
            print(contents)  # noqa: T201
            print("\n\nExpected file:")  # noqa: T201
            print(snippet_contents)  # noqa: T201
        else:
            print(f"Snippet {snippet_path} passed")  # noqa: T201

        assert comparison_fn(
            contents, snippet_contents
        ), "CLI snippets do not match.\nYou may need to run `make regenerate_cli_snippets` in the `dagster/docs` directory.\nYou may also use `make test_cli_snippets_simulate_bk` to simulate the CI environment locally."


def create_file(
    file_path: Union[Path, str],
    contents: str,
    snippet_path: Optional[Path] = None,
):
    """Create a file with the given contents. If `snippet_path` is provided, outputs
    the contents to the snippet file too.

    Used for steps where the user is expected to create a file.

    Args:
        file_path (Union[Path, str]): The path to the file to create.
        contents (str): The contents to write to the file.
        snippet_path (Optional[Path]): The path to the snippet file to update.
    """
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    file_path.write_text(contents)
    if snippet_path:
        _assert_matches_or_update_snippet(
            contents=contents,
            snippet_path=snippet_path,
            update_snippets=True,
            snippet_replace_regex=None,
            custom_comparison_fn=None,
        )


def compare_tree_output(actual: str, expected: str) -> bool:
    """Custom command output comparison function for the output of calling
    `tree`. Often the order of the output is different on different platforms, so we
    just check that the filenames are identical rather than the precise tree order or
    structure.
    """
    TREE_PIPE_CHARS = ["│", "├", "└"]
    actual_non_filepath_lines = [
        line
        for line in actual.split("\n")
        if not any(line.strip().startswith(c) for c in TREE_PIPE_CHARS)
    ]
    expected_non_filepath_lines = [
        line
        for line in expected.split("\n")
        if not any(line.strip().startswith(c) for c in TREE_PIPE_CHARS)
    ]

    actual_filepath_lines = [
        line
        for line in actual.split("\n")
        if any(line.strip().startswith(c) for c in TREE_PIPE_CHARS)
    ]
    expected_filepath_lines = [
        line
        for line in expected.split("\n")
        if any(line.strip().startswith(c) for c in TREE_PIPE_CHARS)
    ]

    # strip out non-filename text from each of the filepath lines
    actual_filepath_lines = sorted(
        [line.strip().rsplit(" ", 1)[1] for line in actual_filepath_lines]
    )
    expected_filepath_lines = sorted(
        [line.strip().rsplit(" ", 1)[1] for line in expected_filepath_lines]
    )

    return (
        actual_non_filepath_lines == expected_non_filepath_lines
        and actual_filepath_lines == expected_filepath_lines
    )


def check_file(
    file_path: Union[Path, str],
    snippet_path: Optional[Path] = None,
    update_snippets: Optional[bool] = None,
    snippet_replace_regex: Optional[Sequence[tuple[str, str]]] = None,
):
    """Check that the contents of the file at `file_path` match the contents of the snippet
    at `snippet_path`. If `update_snippets` is `True`, updates the snippet file with the
    contents of the file.

    Used for steps where we want to show the user the contents of a file (e.g. one that's
    generated by the framework, or by output).

    Args:
        file_path (Union[Path, str]): The path to the file to check.
        snippet_path (Optional[Path]): The path to the snippet file to check/update.
        update_snippets (Optional[bool]): Whether to update the snippet file with the file contents.
        snippet_replace_regex (Optional[Sequence[tuple[str, str]]]): A list of regex
            substitution pairs to apply to the file contents before checking it against the snippet.
            Useful to remove dynamic content, e.g. the temporary directory path or timestamps.
    """
    file_path = Path(file_path)
    assert file_path.exists(), f"Expected file {file_path} to exist"
    contents = file_path.read_text()

    if snippet_path:
        assert update_snippets is not None

        _assert_matches_or_update_snippet(
            contents=contents,
            snippet_path=snippet_path,
            update_snippets=update_snippets,
            snippet_replace_regex=snippet_replace_regex,
            custom_comparison_fn=None,
        )


def run_command_and_snippet_output(
    cmd: Union[str, Sequence[str]],
    snippet_path: Optional[Path] = None,
    update_snippets: Optional[bool] = None,
    snippet_replace_regex: Optional[Sequence[tuple[str, str]]] = None,
    custom_comparison_fn: Optional[Callable[[str, str], bool]] = None,
    ignore_output: bool = False,
    expect_error: bool = False,
):
    """Run the given command and check that the output matches the contents of the snippet
    at `snippet_path`. If `update_snippets` is `True`, updates the snippet file with the
    output of the command.

    Args:
        cmd (Union[str, Sequence[str]): The command to run.
        snippet_path (Optional[Path]): The path to the snippet file to check/update.
        update_snippets (Optional[bool]): Whether to update the snippet file with the output.
        snippet_replace_regex (Optional[Sequence[tuple[str, str]]]): A list of regex
            substitution pairs to apply to the output before checking it against the snippet.
            Useful to remove dynamic content, e.g. the temporary directory path or timestamps.
        custom_comparison_fn (Optional[Callable]): A function that takes the output of the
            command and the snippet contents and returns whether they match. Useful for some
            commands (e.g. tree) where the output is frustratingly platform-dependent.
        ignore_output (bool): Whether to ignore the output of the command when updating the snippet.
            Useful when the output is too verbose or not meaningful.
    """
    assert update_snippets is not None or snippet_path is None

    output = _run_command(cmd, expect_error=expect_error)

    if snippet_path:
        assert update_snippets is not None

        if ignore_output:
            contents = str(cmd)
        else:
            contents = f"{cmd}\n\n{output}"

        _assert_matches_or_update_snippet(
            contents=contents,
            snippet_path=snippet_path,
            update_snippets=update_snippets,
            snippet_replace_regex=snippet_replace_regex,
            custom_comparison_fn=custom_comparison_fn,
        )
