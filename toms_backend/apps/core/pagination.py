from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """Page-number pagination with a client-controlled page size.

    The frontend's data tables pass ``?page=&page_size=``; the response keeps
    DRF's ``count/next/previous/results`` shape.
    """

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 200
