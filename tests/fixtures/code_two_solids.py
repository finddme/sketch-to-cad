from build123d import Compound, Plane, Solid


def build():
    return Compound([Solid.make_box(5, 5, 5), Solid.make_box(5, 5, 5, Plane(origin=(20, 0, 0)))])
