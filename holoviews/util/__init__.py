import inspect

import param

from ..core import DynamicMap, HoloMap, Dimensioned, ViewableElement, StoreOptions, Store
from ..core.options import options_policy
from ..core.operation import Operation
from ..core.util import Aliases  # noqa (API import)
from ..core.operation import OperationCallable
from ..core.spaces import Callable
from ..core import util
from ..streams import Stream
from .settings import OutputSettings


Store.output_settings = OutputSettings

def opts(options, obj=None):
    """
    Utility function to set options either at the global level or on a
    specific object. These two modes are equivalent to the %opts line
    magic and the %%opts cell magic respectively.
    """
    from .parser import OptsSpec
    if obj is None:
        with options_policy(skip_invalid=True, warn_on_skip=False):
            StoreOptions.apply_customizations(OptsSpec.parse(options), Store.options())
    elif not isinstance(obj, Dimensioned):
        return obj
    else:
        return StoreOptions.set_options(obj, OptsSpec.parse(options))


def output(line=None, obj=None, **options):
    """
    Cell magic version acts as a no-op.
    """
    help_prompt = 'For help with hv.util.output call help(hv.util.output)'
    if isinstance(obj, Dimensioned):
        if line:
            options = Store.output_settings._extract_keywords(line, {})
        if 'filename' in options:
            Store.renderers[Store.current_backend].save(obj, options['filename'])
        return obj
    elif obj is not None:
        return obj
    else:
        Store.output_settings.output(line=line, help_prompt=help_prompt, **options)

output.__doc__ = Store.output_settings._generate_docstring()

class Dynamic(param.ParameterizedFunction):
    """
    Dynamically applies a callable to the Elements in any HoloViews
    object. Will return a DynamicMap wrapping the original map object,
    which will lazily evaluate when a key is requested. By default
    Dynamic applies a no-op, making it useful for converting HoloMaps
    to a DynamicMap.

    Any supplied kwargs will be passed to the callable and any streams
    will be instantiated on the returned DynamicMap.
    """

    operation = param.Callable(default=lambda x: x, doc="""
        Operation or user-defined callable to apply dynamically""")

    kwargs = param.Dict(default={}, doc="""
        Keyword arguments passed to the function.""")

    link_inputs = param.Boolean(default=True, doc="""
         If Dynamic is applied to another DynamicMap, determines whether
         linked streams attached to its Callable inputs are
         transferred to the output of the utility.

         For example if the Dynamic utility is applied to a DynamicMap
         with an RangeXY, this switch determines whether the
         corresponding visualization should update this stream with
         range changes originating from the newly generated axes.""")

    shared_data = param.Boolean(default=False, doc="""
        Whether the cloned DynamicMap will share the same cache.""")

    streams = param.List(default=[], doc="""
        List of streams to attach to the returned DynamicMap""")

    def __call__(self, map_obj, **params):
        self.p = param.ParamOverrides(self, params)
        callback = self._dynamic_operation(map_obj)
        streams = self._get_streams(map_obj)
        if isinstance(map_obj, DynamicMap):
            dmap = map_obj.clone(callback=callback, shared_data=self.p.shared_data,
                                 streams=streams)
        else:
            dmap = self._make_dynamic(map_obj, callback, streams)
        return dmap


    def _get_streams(self, map_obj):
        """
        Generates a list of streams to attach to the returned DynamicMap.
        If the input is a DynamicMap any streams that are supplying values
        for the key dimension of the input are inherited. And the list
        of supplied stream classes and instances are processed and
        added to the list.
        """
        streams = []
        for stream in self.p.streams:
            if inspect.isclass(stream) and issubclass(stream, Stream):
                stream = stream()
            elif not isinstance(stream, Stream):
                raise ValueError('Streams must be Stream classes or instances')
            if isinstance(self.p.operation, Operation):
                updates = {k: self.p.operation.p.get(k) for k, v in stream.contents.items()
                           if v is None and k in self.p.operation.p}
                if updates:
                    reverse = {v: k for k, v in stream._rename.items()}
                    stream.update(**{reverse.get(k, k): v for k, v in updates.items()})
            streams.append(stream)
        if isinstance(map_obj, DynamicMap):
            dim_streams = util.dimensioned_streams(map_obj)
            streams = list(util.unique_iterator(streams + dim_streams))
        return streams


    def _process(self, element, key=None):
        if isinstance(self.p.operation, Operation):
            kwargs = {k: v for k, v in self.p.kwargs.items()
                      if k in self.p.operation.params()}
            return self.p.operation.process_element(element, key, **kwargs)
        else:
            return self.p.operation(element, **self.p.kwargs)


    def _dynamic_operation(self, map_obj):
        """
        Generate function to dynamically apply the operation.
        Wraps an existing HoloMap or DynamicMap.
        """
        if not isinstance(map_obj, DynamicMap):
            def dynamic_operation(*key, **kwargs):
                self.p.kwargs.update(kwargs)
                obj = map_obj[key] if isinstance(map_obj, HoloMap) else map_obj
                return self._process(obj, key)
        else:
            def dynamic_operation(*key, **kwargs):
                self.p.kwargs.update(kwargs)
                return self._process(map_obj[key], key)
        if isinstance(self.p.operation, Operation):
            return OperationCallable(dynamic_operation, inputs=[map_obj],
                                     link_inputs=self.p.link_inputs,
                                     operation=self.p.operation)
        else:
            return Callable(dynamic_operation, inputs=[map_obj],
                            link_inputs=self.p.link_inputs)


    def _make_dynamic(self, hmap, dynamic_fn, streams):
        """
        Accepts a HoloMap and a dynamic callback function creating
        an equivalent DynamicMap from the HoloMap.
        """
        if isinstance(hmap, ViewableElement):
            return DynamicMap(dynamic_fn, streams=streams)
        dim_values = zip(*hmap.data.keys())
        params = util.get_param_values(hmap)
        kdims = [d(values=list(util.unique_iterator(values))) for d, values in
                 zip(hmap.kdims, dim_values)]
        return DynamicMap(dynamic_fn, streams=streams, **dict(params, kdims=kdims))
