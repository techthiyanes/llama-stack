# ruff: noqa
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.
import json
import re
import time
from typing import Any

# Comment out for now until we actually use the rest checker in evals
# import requests  # Do not remove this import even though it seems to be unused. It's used in the executable_checker_rest function.


class NoAPIKeyError(Exception):
    def __init__(self):
        self.message = "❗️Please fill in the API keys in the function_credential_config.json file. If you do not provide the API keys, the executable test category results will be inaccurate."
        super().__init__(self.message)


REAL_TIME_MATCH_ALLOWED_DIFFERENCE = 0.2


JAVA_TYPE_CONVERSION = {
    "byte": int,
    "short": int,
    "integer": int,
    "float": float,
    "double": float,
    "long": int,
    "boolean": bool,
    "char": str,
    "Array": list,
    "ArrayList": list,
    "Set": set,
    "HashMap": dict,
    "Hashtable": dict,
    "Queue": list,  # this can be `queue.Queue` as well, for simplicity we check with list
    "Stack": list,
    "String": str,
    "any": str,
}

JS_TYPE_CONVERSION = {
    "String": str,
    "integer": int,
    "float": float,
    "Bigint": int,
    "Boolean": bool,
    "dict": dict,
    "array": list,
    "any": str,
}

# We switch to conditional import for the following two imports to avoid unnecessary installations.
# User doesn't need to setup the tree-sitter packages if they are not running the test for that language.
# from js_type_converter import js_type_converter
# from java_type_converter import java_type_converter

PYTHON_TYPE_MAPPING = {
    "string": str,
    "integer": int,
    "float": float,
    "boolean": bool,
    "array": list,
    "tuple": list,
    "dict": dict,
    "any": str,
}

# This is the list of types that we need to recursively check its values
PYTHON_NESTED_TYPE_CHECK_LIST = ["array", "tuple"]


NESTED_CONVERSION_TYPE_LIST = ["Array", "ArrayList", "array"]


#### Helper functions for AST ####
def find_description(func_descriptions, name):
    if type(func_descriptions) == list:
        for func_description in func_descriptions:
            if func_description["name"] == name:
                return func_description
        return None
    else:
        # it is a dict, there is only one function
        return func_descriptions


def get_possible_answer_type(possible_answer: list):
    for answer in possible_answer:
        if answer != "":  # Optional parameter
            return type(answer)
    return None


def type_checker(
    param: str,
    value,
    possible_answer: list,
    expected_type_description: str,
    expected_type_converted,
    nested_type_converted,
):
    # NOTE: This type checker only supports nested type checking for one level deep.
    # We didn't implement recursive type checking for nested types, as it's not needed for the current use case and it's very complex.

    result: Any = {
        "valid": True,
        "error": [],
        "is_variable": False,
        "error_type": "type_error:simple",
    }

    is_variable = False
    # check for the case where a variable is used instead of a actual value.
    # use the type in possible_answer as the expected type
    possible_answer_type = get_possible_answer_type(possible_answer)
    # if possible_answer only contains optional parameters, we can't determine the type
    if possible_answer_type != None:
        # we are being precise here.
        # in fact, possible_answer_type should always be string, as that's how we treat varibale in possible_answer
        if possible_answer_type != expected_type_converted:
            is_variable = True

    # value is the same type as in function description
    if type(value) == expected_type_converted:
        # We don't need to do recursive check for simple types
        if nested_type_converted == None:
            result["is_variable"] = is_variable
            return result
        else:
            for possible_answer_item in possible_answer:
                flag = True  # Each parameter should match to at least one possible answer type.
                # Here, we assume that each item should be the same type. We could also relax it.
                if type(possible_answer_item) == list:
                    for value_item in value:
                        checker_result = type_checker(
                            param,
                            value_item,
                            possible_answer_item,
                            str(nested_type_converted),
                            nested_type_converted,
                            None,
                        )
                        if not checker_result["valid"]:
                            flag = False
                            break

                if flag:
                    return {"valid": True, "error": [], "is_variable": is_variable}

            result["valid"] = False
            result["error"] = [
                f"Nested type checking failed for parameter {repr(param)}. Expected outer type {expected_type_description} with inner type {str(nested_type_converted)}. Parameter value: {repr(value)}."
            ]
            result["error_type"] = "type_error:nested"

    # value is not as expected, check for the case where a variable is used instead of a actual value
    # use the type in possible_answer as the expected type
    possible_answer_type = get_possible_answer_type(possible_answer)
    # if possible_answer only contains optional parameters, we can't determine the type
    if possible_answer_type != None:
        # we are being precise here.
        # in fact, possible_answer_type should always be string, as that's how we treat varibale in possible_answer
        if type(value) == possible_answer_type:
            result["is_variable"] = True
            return result

    result["valid"] = False
    result["error"].append(
        f"Incorrect type for parameter {repr(param)}. Expected type {expected_type_description}, got {type(value).__name__}. Parameter value: {repr(value)}."
    )
    result["error_type"] = "type_error:simple"
    return result


def standardize_string(input_string: str):
    # This function standardizes the string by removing all the spaces, ",./-_*^" punctuation, and converting it to lowercase
    # It will also convert all the single quotes to double quotes
    # This is used to compare the model output with the possible answers
    # We don't want to punish model for answer like April 1, 2024 vs April 1,2024, vs April 1 2024
    regex_string = r"[ \,\.\/\-\_\*\^]"
    return re.sub(regex_string, "", input_string).lower().replace("'", '"')


def string_checker(param: str, model_output: str, possible_answer: list):
    standardize_possible_answer = []
    standardize_model_output = standardize_string(model_output)
    for i in range(len(possible_answer)):
        if type(possible_answer[i]) == str:
            standardize_possible_answer.append(standardize_string(possible_answer[i]))

    if standardize_model_output not in standardize_possible_answer:
        return {
            "valid": False,
            "error": [
                f"Invalid value for parameter {repr(param)}: {repr(model_output)}. Expected one of {possible_answer}. Case insensitive."
            ],
            "error_type": "value_error:string",
        }

    return {"valid": True, "error": []}


def list_checker(param: str, model_output: list, possible_answer: list):
    # Convert the tuple to a list

    standardize_model_output = list(model_output)

    # If the element in the list is a string, we need to standardize it
    for i in range(len(standardize_model_output)):
        if type(standardize_model_output[i]) == str:
            standardize_model_output[i] = standardize_string(model_output[i])

    standardize_possible_answer: Any = []
    # We also need to standardize the possible answers
    for i in range(len(possible_answer)):
        standardize_possible_answer.append([])
        for j in range(len(possible_answer[i])):
            if type(possible_answer[i][j]) == str:
                standardize_possible_answer[i].append(standardize_string(possible_answer[i][j]))
            else:
                standardize_possible_answer[i].append(possible_answer[i][j])

    if standardize_model_output not in standardize_possible_answer:
        return {
            "valid": False,
            "error": [
                f"Invalid value for parameter {repr(param)}: {repr(model_output)}. Expected one of {possible_answer}."
            ],
            "error_type": "value_error:list/tuple",
        }

    return {"valid": True, "error": []}


def dict_checker(param: str, model_output: dict, possible_answers: list):
    # This function works for simple dictionaries, but not dictionaries with nested dictionaries.
    # The current dataset only contains simple dictionaries, so this is sufficient.

    result = {"valid": False, "error": [], "error_type": "dict_checker:unclear"}
    for i in range(len(possible_answers)):
        if possible_answers[i] == "":
            continue

        result = {"valid": False, "error": [], "error_type": "dict_checker:unclear"}

        flag = True

        possible_answer = possible_answers[i]
        # possible_anwer is a single dictionary

        for key, value in model_output.items():
            if key not in possible_answer:
                result["valid"] = False
                result["error"].append(f"Unexpected dict key parameter: '{key}'.")  # type: ignore[attr-defined]
                result["error_type"] = "value_error:dict_key"
                flag = False
                break

            standardize_value = value
            # If the value is a string, we need to standardize it
            if type(value) == str:
                standardize_value = standardize_string(value)

            # We also need to standardize the possible answers if they are string
            standardize_possible_answer = []
            for i in range(len(possible_answer[key])):
                if type(possible_answer[key][i]) == str:
                    standardize_possible_answer.append(standardize_string(possible_answer[key][i]))
                else:
                    standardize_possible_answer.append(possible_answer[key][i])

            if standardize_value not in standardize_possible_answer:
                result["valid"] = False
                result["error"].append(  # type: ignore[attr-defined]
                    f"Invalid value for parameter {repr(key)}: {repr(value)}. Expected one of {standardize_possible_answer}."
                )
                result["error_type"] = "value_error:dict_value"
                flag = False
                break

        for key, value in possible_answer.items():
            if key not in model_output and "" not in value:
                result["valid"] = False
                result["error"].append(f"Missing dict key parameter: '{key}'.")  # type: ignore[attr-defined]
                result["error_type"] = "value_error:dict_key"
                flag = False
                break

        if flag:
            return {"valid": True, "error": []}

    return result


def list_dict_checker(param: str, model_output: list, possible_answers: list):
    # This function takes in a list of dictionaries and checks if each dictionary is valid
    # The order of the dictionaries in the list must match the order of the possible answers

    result = {"valid": False, "error": [], "error_type": "list_dict_checker:unclear"}

    for answer_index in range(len(possible_answers)):
        flag = True  # True means so far, all dictionaries are valid

        # Only proceed if the number of dictionaries in the list matches the number of dictionaries in the possible answers
        if len(model_output) != len(possible_answers[answer_index]):
            result["valid"] = False
            result["error"] = ["Wrong number of dictionaries in the list."]
            result["error_type"] = "value_error:list_dict_count"
            flag = False
            continue

        for dict_index in range(len(model_output)):
            result = dict_checker(
                param,
                model_output[dict_index],
                [possible_answers[answer_index][dict_index]],
            )
            if not result["valid"]:
                flag = False
                break
        if flag:
            return {"valid": True, "error": []}

    return result


def simple_function_checker(
    func_description: dict,
    model_output: dict,
    possible_answer: dict,
    language: str,
    model_name: str,
):
    possible_answer = list(possible_answer.values())[0]
    # Extract function name and parameters details
    func_name = func_description["name"]
    param_details = func_description["parameters"]["properties"]
    required_params = func_description["parameters"]["required"]

    # Initialize a result dictionary
    result = {
        "valid": True,
        "error": [],
        "error_type": "simple_function_checker:unclear",
    }

    # Check if function name matches
    if func_name not in model_output:
        result["valid"] = False
        result["error"].append(  # type: ignore[attr-defined]
            f"Function name {repr(func_name)} not found in model output."
        )
        result["error_type"] = "simple_function_checker:wrong_func_name"
        return result

    model_params = model_output[func_name]

    # Check for required parameters in model output
    for param in required_params:
        if param not in model_params:
            result["valid"] = False
            result["error"].append(f"Missing required parameter: {repr(param)}.")  # type: ignore[attr-defined]
            result["error_type"] = "simple_function_checker:missing_required"
            return result

    # Validate types and values for each parameter in model output
    for param, value in model_params.items():
        if param not in param_details or param not in possible_answer:
            result["valid"] = False
            result["error"].append(f"Unexpected parameter: {repr(param)}.")  # type: ignore[attr-defined]
            result["error_type"] = "simple_function_checker:unexpected_param"
            return result

        full_param_details = param_details[param]
        expected_type_description = full_param_details["type"]  # This is a string
        is_variable = False
        nested_type_converted = None

        if language == "Java":
            from evals.utils.bfcl.java_type_converter import java_type_converter

            expected_type_converted = JAVA_TYPE_CONVERSION[expected_type_description]

            if expected_type_description in JAVA_TYPE_CONVERSION:
                if type(value) != str:
                    result["valid"] = False
                    result["error"].append(  # type: ignore[attr-defined]
                        f"Incorrect type for parameter {repr(param)}. Expected type String, got {type(value).__name__}. Parameter value: {repr(value)}."
                    )
                    result["error_type"] = "type_error:java"
                    return result

                if expected_type_description in NESTED_CONVERSION_TYPE_LIST:
                    nested_type = param_details[param]["items"]["type"]
                    nested_type_converted = JAVA_TYPE_CONVERSION[nested_type]
                    value = java_type_converter(value, expected_type_description, nested_type)
                else:
                    value = java_type_converter(value, expected_type_description)

        elif language == "JavaScript":
            from evals.utils.bfcl.js_type_converter import js_type_converter

            expected_type_converted = JS_TYPE_CONVERSION[expected_type_description]

            if expected_type_description in JS_TYPE_CONVERSION:
                if type(value) != str:
                    result["valid"] = False
                    result["error"].append(  # type: ignore[attr-defined]
                        f"Incorrect type for parameter {repr(param)}. Expected type String, got {type(value).__name__}. Parameter value: {repr(value)}."
                    )
                    result["error_type"] = "type_error:js"
                    return result

                if expected_type_description in NESTED_CONVERSION_TYPE_LIST:
                    nested_type = param_details[param]["items"]["type"]
                    nested_type_converted = JS_TYPE_CONVERSION[nested_type]
                    value = js_type_converter(value, expected_type_description, nested_type)
                else:
                    value = js_type_converter(value, expected_type_description)

        elif language == "Python":
            expected_type_converted = PYTHON_TYPE_MAPPING[expected_type_description]
            if expected_type_description in PYTHON_NESTED_TYPE_CHECK_LIST:
                nested_type = param_details[param]["items"]["type"]
                nested_type_converted = PYTHON_TYPE_MAPPING[nested_type]

        # We convert all tuple value to list when the expected type is tuple.
        # The conversion is necessary because any tuple in the possible answer would become a list after being processed through json.dump() and json.load().
        # This does introduce some false positive (eg, when the model provides a list value instead of tuple). We hope to find a better solution in the future.
        if expected_type_description == "tuple" and type(value) == tuple:
            value = list(value)

        # Allow python auto conversion from int to float
        if language == "Python" and expected_type_description == "float" and type(value) == int:
            value = float(value)

        # Type checking
        # In fact, we only check for Python here.
        # Type check for other languages are handled by the type converter, and so their value (after conversion) is always correct.
        type_check_result = type_checker(
            param,
            value,
            possible_answer[param],
            expected_type_description,
            expected_type_converted,
            nested_type_converted,
        )
        is_variable = type_check_result["is_variable"]
        if not type_check_result["valid"]:
            return type_check_result

        # It doesn't make sense to special handle dictionaries and list of dictionaries if the value is a variable.
        # We can just treat the variable as a string and use the normal flow.
        if not is_variable:
            # Special handle for dictionaries
            if expected_type_converted == dict:
                result = dict_checker(param, value, possible_answer[param])
                if not result["valid"]:
                    return result
                continue

            # Special handle for list of dictionaries
            elif expected_type_converted == list and nested_type_converted == dict:
                result = list_dict_checker(param, value, possible_answer[param])
                if not result["valid"]:
                    return result
                continue

            # Special handle for strings
            elif expected_type_converted == str:
                # We don't check for case sensitivity for string, as long as it's not a variable
                result = string_checker(param, value, possible_answer[param])
                if not result["valid"]:
                    return result
                continue

            elif expected_type_converted == list:
                result = list_checker(param, value, possible_answer[param])
                if not result["valid"]:
                    return result
                continue

        # Check if the value is within the possible answers
        if value not in possible_answer[param]:
            result["valid"] = False
            result["error"].append(  # type: ignore[attr-defined]
                f"Invalid value for parameter {repr(param)}: {repr(value)}. Expected one of {possible_answer[param]}."
            )
            result["error_type"] = "value_error:others"
            return result

    # Check for optional parameters not provided but allowed
    for param in possible_answer:
        if param not in model_params and "" not in possible_answer[param]:
            result["valid"] = False
            result["error"].append(  # type: ignore[attr-defined]
                f"Optional parameter {repr(param)} not provided and not marked as optional."
            )
            result["error_type"] = "simple_function_checker:missing_optional"
            return result

    return result


def parallel_function_checker_enforce_order(
    func_descriptions: list,
    model_output: list,
    possible_answers: dict,
    language: str,
    model_name: str,
):
    if len(model_output) != len(possible_answers):
        return {
            "valid": False,
            "error": ["Wrong number of functions."],
            "error_type": "parallel_function_checker_enforce_order:wrong_count",
        }

    func_name_list = list(possible_answers.keys())
    possible_answers_list = []

    for key, value in possible_answers.items():
        possible_answers_list.append({key: value})

    for i in range(len(possible_answers_list)):
        func_description = find_description(func_descriptions, func_name_list[i])

        result = simple_function_checker(
            func_description,
            model_output[i],
            possible_answers_list[i],
            language,
            model_name,
        )
        if not result["valid"]:
            return result

    return {"valid": True, "error": []}


def parallel_function_checker_no_order(
    func_descriptions: list,
    model_output: list,
    possible_answers: list,
    language: str,
    model_name: str,
):
    if len(model_output) != len(possible_answers):
        return {
            "valid": False,
            "error": ["Wrong number of functions."],
            "error_type": "parallel_function_checker_no_order:wrong_count",
        }

    matched_indices = []

    # We go throught the possible answers one by one, and eliminate the model output that matches the possible answer
    # It must be this way because we need ground truth to fetch the correct function description
    for i in range(len(possible_answers)):
        # possible_answers[i] is a dictionary with only one key
        func_name_expected = list(possible_answers[i].keys())[0]
        func_description = find_description(func_descriptions, func_name_expected)

        all_errors = []

        for index in range(len(model_output)):
            if index in matched_indices:
                continue

            result = simple_function_checker(
                func_description,
                model_output[index],
                possible_answers[i],
                language,
                model_name,
            )

            if result["valid"]:
                matched_indices.append(index)
                break
            else:
                all_errors.append(
                    {
                        f"Model Result Index {index}": {
                            "sub_error": result["error"],
                            "sub_error_type": result["error_type"],
                            "model_output_item": model_output[index],
                            "possible_answer_item": possible_answers[i],
                        }
                    }
                )

        if not result["valid"]:
            considered_indices = [i for i in range(len(model_output)) if i not in matched_indices]
            all_errors.insert(
                0,
                f"Could not find a matching function among index {considered_indices} of model output for index {i} of possible answers.",  # type: ignore[arg-type]
            )
            return {
                "valid": False,
                "error": all_errors,
                "error_type": "parallel_function_checker_no_order:cannot_find_match",
            }

    return {"valid": True, "error": []}


def multiple_function_checker(
    func_descriptions: list,
    model_output: list,
    possible_answers: list,
    language: str,
    model_name: str,
):
    if len(model_output) != len(possible_answers):
        return {
            "valid": False,
            "error": ["Wrong number of functions."],
            "error_type": "multiple_function_checker:wrong_count",
        }

    # possible_answers is a list of only one dictionary with only one key
    func_name_expected = list(possible_answers[0].keys())[0]
    func_description = find_description(func_descriptions, func_name_expected)
    return simple_function_checker(
        func_description,
        model_output[0],
        possible_answers[0],
        language,
        model_name,
    )


def patten_matcher(exec_output, expected_result, function_call, is_sanity_check):
    result = {"valid": True, "error": [], "error_type": "executable_checker:unclear"}

    if type(exec_output) != type(expected_result):
        return {
            "valid": False,
            "error": [
                f"Wrong execution result type for {repr(function_call)}. Expected type: {type(expected_result)}, but got: {type(exec_output)}."
            ],
            "error_type": "executable_checker:wrong_result_type",
            "model_executed_output": exec_output,
        }
    if type(exec_output) == dict:
        # We loose the requirement for the sanity check as the expected result used in the sanity check might not be the most up-to-date one.
        # This happens when the key is a timestamp or a random number.
        if is_sanity_check:
            if len(exec_output) != len(expected_result):
                return {
                    "valid": False,
                    "error": [
                        f"Wrong execution result pattern for {repr(function_call)}. Expect type Dict, but wrong number of elements in the output. Expected length: {len(expected_result)}, but got: {len(exec_output)}."
                    ],
                    "error_type": "executable_checker:wrong_result_type:dict_length",
                    "model_executed_output": exec_output,
                }
            else:
                return result

        for key, value in expected_result.items():
            if key not in exec_output:
                return {
                    "valid": False,
                    "error": [
                        f"Wrong execution result pattern for {repr(function_call)}. Expect type Dict, but key {repr(key)} not found in the model output."
                    ],
                    "error_type": "executable_checker:wrong_result_type:dict_key_not_found",
                    "model_executed_output": exec_output,
                }
        for key, value in exec_output.items():
            if key not in expected_result:
                return {
                    "valid": False,
                    "error": [
                        f"Wrong execution result pattern for {repr(function_call)}. Expect type Dict, but key {repr(key)} not expected in the model output."
                    ],
                    "error_type": "executable_checker:wrong_result_type:dict_extra_key",
                    "model_executed_output": exec_output,
                }
    if type(exec_output) == list:
        if len(exec_output) != len(expected_result):
            return {
                "valid": False,
                "error": [
                    f"Wrong execution result pattern for {repr(function_call)}. Expect type list, but wrong number of elements in the output. Expected length: {len(expected_result)}, but got: {len(exec_output)}."
                ],
                "error_type": "executable_checker:wrong_result_type:list_length",
                "model_executed_output": exec_output,
            }
    return result


#### Helper functions for Exec ####
def executable_checker_simple(
    function_call: str,
    expected_result,
    expected_result_type: str,
    is_sanity_check=False,
):
    result = {"valid": True, "error": [], "error_type": "executable_checker:unclear"}

    exec_dict: Any = {}

    try:
        exec(
            "from executable_python_function import *" + "\nresult=" + function_call,
            exec_dict,
        )
        exec_output = exec_dict["result"]
    except NoAPIKeyError as e:
        raise e
    except Exception as e:
        result["valid"] = False
        result["error"].append(  # type: ignore[attr-defined]
            f"Error in execution: {repr(function_call)}. Error: {str(e)}"
        )
        result["error_type"] = "executable_checker:execution_error"
        return result

    # We need to special handle the case where the execution result is a tuple and convert it to a list
    # Because when json is stored, the tuple is converted to a list, and so the expected result is a list when loaded from json
    if isinstance(exec_output, tuple):
        exec_output = list(exec_output)

    if expected_result_type == "exact_match":
        if exec_output != expected_result:
            result["valid"] = False
            result["error"].append(  # type: ignore[attr-defined]
                f"Wrong execution result for {repr(function_call)}. Expected: {expected_result}, but got: {exec_output}."
            )
            result["error_type"] = "executable_checker:wrong_result"
            result["model_executed_output"] = exec_output
            return result

    elif expected_result_type == "real_time_match":
        # Allow for 5% difference
        if (type(expected_result) == float or type(expected_result) == int) and (
            type(exec_output) == float or type(exec_output) == int
        ):
            if not (
                expected_result * (1 - REAL_TIME_MATCH_ALLOWED_DIFFERENCE)
                <= exec_output
                <= expected_result * (1 + REAL_TIME_MATCH_ALLOWED_DIFFERENCE)
            ):
                result["valid"] = False
                result["error"].append(  # type: ignore[attr-defined]
                    f"Wrong execution result for {repr(function_call)}. Expected: {expected_result}, but got: {exec_output}. {REAL_TIME_MATCH_ALLOWED_DIFFERENCE * 100}% difference allowed."
                )
                result["error_type"] = "executable_checker:wrong_result_real_time"
                result["model_executed_output"] = exec_output
                return result
        else:
            result["valid"] = False
            result["error"].append(  # type: ignore[attr-defined]
                f"Wrong execution result for {repr(function_call)}. Expected: {expected_result}, but got: {exec_output}. Type needs to be float or int for real time match criteria."
            )
            result["error_type"] = "executable_checker:wrong_result_real_time"
            result["model_executed_output"] = exec_output
            return result

    else:
        # structural match
        pattern_match_result = patten_matcher(exec_output, expected_result, function_call, is_sanity_check)
        if not pattern_match_result["valid"]:
            return pattern_match_result

    return result


def executable_checker_parallel_no_order(
    decoded_result: list, expected_exec_result: list, expected_exec_result_type: list
):
    if len(decoded_result) != len(expected_exec_result):
        return {
            "valid": False,
            "error": [
                f"Wrong number of functions provided. Expected {len(expected_exec_result)}, but got {len(decoded_result)}."
            ],
            "error_type": "value_error:exec_result_count",
        }

    matched_indices = []
    for i in range(len(expected_exec_result)):
        all_errors = []
        for index in range(len(decoded_result)):
            if index in matched_indices:
                continue

            result = executable_checker_simple(
                decoded_result[index],
                expected_exec_result[i],
                expected_exec_result_type[i],
                False,
            )

            if result["valid"]:
                matched_indices.append(index)
                break
            else:
                all_errors.append(
                    {
                        f"Model Result Index {index}": {
                            "sub_error": result["error"],
                            "sub_error_type": result["error_type"],
                            "model_executed_output": (
                                result["model_executed_output"] if "model_executed_output" in result else None
                            ),
                        }
                    }
                )

        if not result["valid"]:
            considered_indices = [i for i in range(len(decoded_result)) if i not in matched_indices]
            all_errors.insert(
                0,
                f"Could not find a matching function among index {considered_indices} of model output for index {i} of possible answers.",  # type: ignore[arg-type]
            )
            return {
                "valid": False,
                "error": all_errors,
                "error_type": "executable_checker:cannot_find_match",
            }

    return {"valid": True, "error": [], "error_type": "executable_checker:unclear"}


#### Main function ####
def executable_checker_rest(func_call, idx):
    # Move this here for now to avoid needing to read this file / fix paths to be relative to dataset_dir. Fix when it's actually needed / used.
    EVAL_GROUND_TRUTH_PATH = "/mnt/wsfuse/fair_llm_v2/datasets/eval/bfcl/rest-eval-response_v5.jsonl"  # Ground truth file for v5 for rest execution
    with open(EVAL_GROUND_TRUTH_PATH, "r") as f:
        EVAL_GROUND_TRUTH = f.readlines()
    if "https://geocode.maps.co" in func_call:
        time.sleep(2)
    if "requests_get" in func_call:
        func_call = func_call.replace("requests_get", "requests.get")
    try:
        response = eval(func_call)
    except Exception as e:
        return {
            "valid": False,
            "error": [f"Execution failed. {str(e)}"],
            "error_type": "executable_checker_rest:execution_error",
        }

    try:
        if response.status_code == 200:
            eval_GT_json = json.loads(EVAL_GROUND_TRUTH[idx])
            try:
                if isinstance(eval_GT_json, dict):
                    if isinstance(response.json(), dict):
                        if set(eval_GT_json.keys()) == set(response.json().keys()):
                            return {"valid": True, "error": [], "error_type": ""}
                        return {
                            "valid": False,
                            "error": ["Key inconsistency"],
                            "error_type": "executable_checker_rest:wrong_key",
                        }
                    return {
                        "valid": False,
                        "error": [f"Expected dictionary, but got {type(response.json())}"],
                        "error_type": "executable_checker_rest:wrong_type",
                    }

                elif isinstance(eval_GT_json, list):
                    if isinstance(response.json(), list):
                        if len(eval_GT_json) != len(response.json()):
                            return {
                                "valid": False,
                                "error": [f"Response list length inconsistency."],
                                "error_type": "value_error:exec_result_rest_count",
                            }

                        else:
                            for i in range(len(eval_GT_json)):
                                if set(eval_GT_json[i].keys()) != set(response.json()[i].keys()):
                                    return {
                                        "valid": False,
                                        "error": [f"Key inconsistency"],
                                        "error_type": "executable_checker_rest:wrong_key",
                                    }

                            return {"valid": True, "error": []}
                    else:
                        return {
                            "valid": False,
                            "error": [f"Expected list, but got {type(response.json())}"],
                            "error_type": "executable_checker_rest:wrong_type",
                        }
                return {
                    "valid": False,
                    "error": [f"Expected dict or list, but got {type(response.json())}"],
                    "error_type": "executable_checker_rest:wrong_type",
                }
            except Exception as e:
                return {
                    "valid": False,
                    "error": [
                        f"Error in execution and type checking. Status code: {response.status_code}. Error: {str(e)}"
                    ],
                    "error_type": "executable_checker_rest:response_format_error",
                }
        else:
            return {
                "valid": False,
                "error": [f"Execution result status code is not 200, got {response.status_code}"],
                "error_type": "executable_checker_rest:wrong_status_code",
            }
    except Exception as e:
        return {
            "valid": False,
            "error": [f"Cannot get status code of the response. Error: {str(e)}"],
            "error_type": "executable_checker_rest:cannot_get_status_code",
        }


def ast_checker(func_description, model_output, possible_answer, language, test_category, model_name):
    if "parallel" in test_category:
        return parallel_function_checker_no_order(func_description, model_output, possible_answer, language, model_name)

    elif "multiple" in test_category:
        return multiple_function_checker(func_description, model_output, possible_answer, language, model_name)

    else:
        if len(model_output) != 1:
            return {
                "valid": False,
                "error": ["Wrong number of functions."],
                "error_type": "simple_function_checker:wrong_count",
            }

        return simple_function_checker(
            func_description[0],
            model_output[0],
            possible_answer[0],
            language,
            model_name,
        )


def exec_checker(decoded_result: list, func_description: dict, test_category: str):
    if "multiple" in test_category or "parallel" in test_category:
        return executable_checker_parallel_no_order(
            decoded_result,
            func_description["execution_result"],
            func_description["execution_result_type"],
        )

    else:
        if len(decoded_result) != 1:
            return {
                "valid": False,
                "error": ["Wrong number of functions."],
                "error_type": "simple_exec_checker:wrong_count",
            }
        return executable_checker_simple(
            decoded_result[0],
            func_description["execution_result"][0],
            func_description["execution_result_type"][0],
            False,
        )


def is_empty_output(decoded_output):
    # This function is a patch to the ast decoder for relevance detection
    # Sometimes the ast decoder will parse successfully, but the input doens't really have a function call
    # [], [{}], and anything that is not in function calling format is considered empty (and thus should be marked as correct)
    if not is_function_calling_format_output(decoded_output):
        return True
    if len(decoded_output) == 0:
        return True
    if len(decoded_output) == 1 and len(decoded_output[0]) == 0:
        return True


def is_function_calling_format_output(decoded_output):
    # Ensure the output is a list of dictionaries
    if type(decoded_output) == list:
        for item in decoded_output:
            if type(item) != dict:
                return False
        return True
    return False
