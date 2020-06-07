"""Project spatial geometries and street networks."""

import math

import geopandas as gpd
import networkx as nx
from pyproj import CRS
from shapely.geometry import Point

from . import settings
from . import utils


def project_geometry(geometry, crs=None, to_crs=None, to_latlong=False):
    """
    Project a shapely geometry from its current CRS to another.

    If to_crs is None, project to the UTM CRS for the UTM zone in which the
    geometry's centroid lies. Otherwise project to the CRS defined by to_crs.

    Parameters
    ----------
    geometry : shapely.geometry.Polygon or shapely.geometry.MultiPolygon
        the geometry to project
    crs : dict or string or pyproj.CRS
        the starting CRS of the passed-in geometry. if None, it will be set to
        settings.default_crs
    to_crs : dict or string or pyproj.CRS
        if None, project to UTM zone in which geometry's centroid lies,
        otherwise project to this CRS
    to_latlong : bool
        if True, project to settings.default_crs

    Returns
    -------
    geometry_proj, crs : tuple
        the projected shapely geometry and the crs of the projected geometry
    """
    if crs is None:
        crs = settings.default_crs

    gdf = gpd.GeoDataFrame()
    gdf.crs = crs
    gdf["geometry"] = None
    gdf.loc[0, "geometry"] = geometry
    gdf_proj = project_gdf(gdf, to_crs=to_crs, to_latlong=to_latlong)
    geometry_proj = gdf_proj["geometry"].iloc[0]
    return geometry_proj, gdf_proj.crs


def project_gdf(gdf, to_crs=None, to_latlong=False):
    """
    Project a GeoDataFrame from its current CRS to another.

    If to_crs is None, project to the UTM CRS for the UTM zone in which the
    GeoDataFrame's centroid lies. Otherwise project to the CRS defined by
    to_crs. The simple UTM zone calculation in this function works well for
    most latitudes, but may not work for some extreme northern locations like
    Svalbard or far northern Norway.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        the GeoDataFrame to be projected
    to_crs : dict or string or pyproj.CRS
        if None, project to UTM zone in which gdf's centroid lies, otherwise
        project to this CRS
    to_latlong : bool
        if True, project to settings.default_crs

    Returns
    -------
    gdf_proj : geopandas.GeoDataFrame
        the projected GeoDataFrame
    """
    if gdf.crs is None or len(gdf) < 1:
        raise ValueError("GeoDataFrame cannot be empty and must have a valid CRS")

    # if to_latlong is True, project the gdf to latlong
    if to_latlong:
        latlong_crs = settings.default_crs
        gdf_proj = gdf.to_crs(latlong_crs)
        utils.log("Projected GeoDataFrame to settings.default_crs")

    # else if to_crs was passed-in, project gdf to this CRS
    elif to_crs is not None:
        gdf_proj = gdf.to_crs(to_crs)
        utils.log(f"Projected GeoDataFrame to {to_crs}")

    # otherwise, automatically project the gdf to UTM
    else:
        if CRS.from_user_input(gdf.crs).is_projected:
            raise ValueError("Geometry must be unprojected to calculate UTM zone")

        # calculate longitude of centroid of union of all geometries in gdf
        avg_lng = gdf["geometry"].unary_union.centroid.x

        # calculate UTM zone from avg longitude to define CRS to project to
        utm_zone = int(math.floor((avg_lng + 180) / 6.0) + 1)
        utm_crs = f"+proj=utm +zone={utm_zone} +ellps=WGS84 +datum=WGS84 +units=m +no_defs"

        # project the GeoDataFrame to the UTM CRS
        gdf_proj = gdf.to_crs(utm_crs)
        utils.log(f"Projected GeoDataFrame to UTM-{utm_zone}")

    return gdf_proj


def project_graph(G, to_crs=None):
    """
    Project graph from its current CRS to another.

    If to_crs is None, project the graph to the UTM CRS for the UTM zone in
    which the graph's centroid lies. Otherwise project the graph to the CRS
    defined by to_crs. Note that graph projection can be very slow for very
    large simplified graphs. If you want a projected graph, it's usually
    faster for large graphs if you create the graph with simplify=False, then
    project the graph, and then simplify it.

    Parameters
    ----------
    G : networkx.MultiDiGraph
        the graph to be projected
    to_crs : dict or string or pyproj.CRS
        if None, project graph to UTM zone in which graph centroid lies,
        otherwise project graph to this CRS

    Returns
    -------
    G_proj : networkx.MultiDiGraph
        the projected graph
    """
    G_proj = G.copy()

    # create a GeoDataFrame of the nodes, name it, convert osmid to str
    nodes, data = zip(*G_proj.nodes(data=True))
    gdf_nodes = gpd.GeoDataFrame(list(data), index=nodes)
    gdf_nodes.crs = G_proj.graph["crs"]

    # create new lat-lng columns just to save that data for later reference
    # if they do not already exist (i.e., don't overwrite in subsequent re-projections)
    if "lon" not in gdf_nodes.columns or "lat" not in gdf_nodes.columns:
        gdf_nodes["lon"] = gdf_nodes["x"]
        gdf_nodes["lat"] = gdf_nodes["y"]

    # create a geometry column from x/y columns
    gdf_nodes["geometry"] = gdf_nodes.apply(lambda row: Point(row["x"], row["y"]), axis=1)
    gdf_nodes.set_geometry("geometry", inplace=True)
    utils.log("Created a GeoDataFrame from graph")

    # project the nodes GeoDataFrame
    gdf_nodes_proj = project_gdf(gdf_nodes, to_crs=to_crs)

    # extract data for all edges that have geometry attribute
    edges_with_geom = []
    for u, v, key, data in G_proj.edges(keys=True, data=True):
        if "geometry" in data:
            edges_with_geom.append({"u": u, "v": v, "key": key, "geometry": data["geometry"]})

    # create an edges GeoDataFrame and project it, if there were any edges
    # with a geometry attribute. geom attr only exists if graph has been
    # simplified, otherwise you don't have to project anything for the edges
    # because the nodes still contain all spatial data
    if len(edges_with_geom) > 0:
        gdf_edges = gpd.GeoDataFrame(edges_with_geom)
        gdf_edges.crs = G_proj.graph["crs"]
        gdf_edges_proj = project_gdf(gdf_edges, to_crs=gdf_nodes_proj.crs)

    # extract projected x and y values from the nodes' geometry column
    gdf_nodes_proj["x"] = gdf_nodes_proj["geometry"].map(lambda point: point.x)
    gdf_nodes_proj["y"] = gdf_nodes_proj["geometry"].map(lambda point: point.y)
    gdf_nodes_proj = gdf_nodes_proj.drop("geometry", axis=1)
    utils.log("Extracted projected node geometries from GeoDataFrame")

    # clear the graph to make it a blank slate for the projected data
    edges = list(G_proj.edges(keys=True, data=True))
    G_proj.clear()

    # add the projected nodes and all their attributes to the graph
    G_proj.add_nodes_from(gdf_nodes_proj.index)
    attributes = gdf_nodes_proj.to_dict()
    for label in gdf_nodes_proj.columns:
        nx.set_node_attributes(G_proj, name=label, values=attributes[label])

    # add the edges and all their attributes (including reconstructed geometry,
    # when it exists) to the graph
    for u, v, key, attributes in edges:
        if "geometry" in attributes:
            mask = (
                (gdf_edges_proj["u"] == u)
                & (gdf_edges_proj["v"] == v)
                & (gdf_edges_proj["key"] == key)
            )
            row = gdf_edges_proj[mask]
            attributes["geometry"] = row["geometry"].iloc[0]

        # attributes dict contains key, so we don't need to explicitly pass it here
        G_proj.add_edge(u, v, **attributes)

    # set the graph's CRS attribute to the new, projected CRS and return the
    # projected graph
    G_proj.graph["crs"] = gdf_nodes_proj.crs
    if "streets_per_node" in G.graph:
        G_proj.graph["streets_per_node"] = G.graph["streets_per_node"]
    utils.log("Rebuilt projected graph")
    return G_proj
