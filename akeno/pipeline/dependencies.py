"""Version-pinned dependency graph; no silent substitution with newest available."""


def ordered_components(components):
    result=[]; visiting=set(); done=set()
    def visit(role):
        if role in visiting:raise ValueError('Cyclic pipeline dependency: '+role)
        if role in done:return
        item=components.get(role)
        if not isinstance(item,dict):raise ValueError('Missing pipeline dependency: '+role)
        visiting.add(role)
        for dependency, exact in item.get('requires',{}).items():
            if components.get(dependency,{}).get('version')!=exact:
                raise ValueError('Dependency version mismatch: '+dependency+' requires '+str(exact))
            visit(dependency)
        visiting.remove(role);done.add(role);result.append((role,item))
    for role in components:visit(role)
    return result
