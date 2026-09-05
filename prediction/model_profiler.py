from typing import List, Dict, Any


ALEXNET_LAYERS = [
    {"name": "conv1", "type": "Conv2d", "flops": 34952512, "params": 23296},
    {"name": "conv2", "type": "Conv2d", "flops": 129653760, "params": 307392},
    {"name": "conv3", "type": "Conv2d", "flops": 88473600, "params": 884736},
    {"name": "conv4", "type": "Conv2d", "flops": 129653760, "params": 663552},
    {"name": "conv5", "type": "Conv2d", "flops": 88473600, "params": 442368},
    {"name": "fc1", "type": "Linear", "flops": 37748736, "params": 37748736},
    {"name": "fc2", "type": "Linear", "flops": 16777216, "params": 16777216},
    {"name": "fc3", "type": "Linear", "flops": 4096000, "params": 4096000},
]


def profile_alexnet() -> List[Dict[str, Any]]:
    return ALEXNET_LAYERS


def get_model_layers(model_name: str) -> List[Dict[str, Any]]:
    model_name = model_name.lower()
    if model_name == "alexnet":
        return profile_alexnet()
    else:
        return []


def get_model_partition_points(model_name: str) -> List[int]:
    layers = get_model_layers(model_name)
    if not layers:
        return []
    return list(range(1, len(layers)))


def get_layer_info(model_name: str, layer_name: str) -> Dict[str, Any]:
    layers = get_model_layers(model_name)
    for layer in layers:
        if layer["name"] == layer_name:
            return layer
    return {}


def calculate_partition_latency(latency_matrix: Dict[str, Dict[str, float]],
                                  partition_point: int,
                                  model_name: str) -> Dict[str, float]:
    layers = get_model_layers(model_name)
    if partition_point < 1 or partition_point >= len(layers):
        return {}

    part1_layers = layers[:partition_point]
    part2_layers = layers[partition_point:]

    latency = {}
    for node in next(iter(latency_matrix.values())).keys():
        part1_time = sum(latency_matrix.get(layer["name"], {}).get(node, 0) for layer in part1_layers)
        part2_time = sum(latency_matrix.get(layer["name"], {}).get(node, 0) for layer in part2_layers)
        latency[node] = {
            "part1": part1_time,
            "part2": part2_time,
            "total": part1_time + part2_time
        }

    return latency