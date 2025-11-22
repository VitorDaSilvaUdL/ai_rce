from .getters import get_hot, get_cold
from .data_transform import unify_data
def compare_prod_dem(prod, dem):
    weight = [0.4, 0.3, 0.15, 0.1, 0.05, 0, 0, 0]
    diff = sum(
        w * max(0, float(p_val) - float(d_val)) for p_key, p_val, d_key, d_val, w in zip(prod.keys(), prod.values(), dem.keys(), dem.values(), weight)
    )

    return diff

def compare_future(prod, dem):
    cold_d = unify_data(get_cold(dem,1))
    hot_d = unify_data(get_hot(dem,1))
    cold_p = get_cold(prod,0)
    hot_p = get_hot(prod,0)

    comp_c = compare_prod_dem(cold_p, cold_d)
    comp_h = compare_prod_dem(hot_p, hot_d)

    return comp_c, comp_h

def select_mode(comp_c, comp_h):
    if comp_c > comp_h:
        return 0
    return 1

def set_action(comp):
    if comp >= 0:
        return 1
    return 0

def change_mode(new, old):
    if new != old:
        "ToDo --> Change mode real"
        old = new
    return old
