/*
OpenIO SDS gridd
Copyright (C) 2014 Worldine, original work as part of Redcurrant
Copyright (C) 2015 OpenIO, modified as part of OpenIO Software Defined Storage

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

#include <stdlib.h>
#include <string.h>
#include <sys/time.h>

#include <metautils/lib/metautils.h>
#include <metautils/lib/metacomm.h>

#include "./server_internals.h"
#include "./message_handler.h"

#define MS_REPLY_TIMEOUT 1000

/*
 * REPLY MANAGEMENT:
 *
 * The server provides a simple API to answer to MESSAGE request, to wrap
 * the socket management.
 */

#define REPLYCTX_CLEANHEADER(ctx) \
do {\
	if (ctx->header.msg)\
		g_free(ctx->header.msg);\
	memset(&(ctx->header), 0x00, sizeof(ctx->header));\
} while (0)

#define REPLYCTX_CLEANBODY(ctx) \
do {\
	if (ctx->body.buffer)\
	{\
		if (ctx->body.copy)\
			g_free(ctx->body.buffer);\
	}\
	memset(&(ctx->body), 0x00, sizeof(ctx->body));\
} while (0)

void
reply_context_clear (struct reply_context_s *ctx, gboolean all)
{
	if (!ctx)
		return;
	if (all) {
		/*free the warnings*/
		if (ctx->warning)
			g_error_free (ctx->warning);
		ctx->warning = NULL;
		/*free the headers too*/
		if (ctx->extra_headers) {
			g_hash_table_destroy( ctx->extra_headers);
			ctx->extra_headers = NULL;
		}
	}

	REPLYCTX_CLEANHEADER(ctx);
	REPLYCTX_CLEANBODY(ctx);
}

void
reply_context_set_message (struct reply_context_s *ctx, gint code, const gchar *msg)
{
	if (!ctx)
		return;
	REPLYCTX_CLEANHEADER(ctx);
	ctx->header.code = code;
	ctx->header.msg = msg ? g_strdup(msg) : NULL;
	TRACE("couple message/code set to (%i %s)", ctx->header.code, ctx->header.msg);
}

void
reply_context_set_body (struct reply_context_s *ctx, void *body, gsize bodySize, guint32 flags)
{
	if (!ctx)
		return;
	REPLYCTX_CLEANBODY(ctx);
	if (body && bodySize>0) {
		ctx->body.copy = (flags & REPLYCTX_DESTROY_ON_CLEAN) | (flags & REPLYCTX_COPY);
		ctx->body.size = bodySize;
		ctx->body.buffer = flags & REPLYCTX_COPY ? g_memdup(body, bodySize) : body;
		TRACE("content set to (%p size=%"G_GSIZE_FORMAT")", ctx->body.buffer, ctx->body.size);
	}
}

static void
reply_context_add_bufheader_in_reply(struct reply_context_s *ctx, const char *k, const guint8 *v, gsize vlen)
{
	char *newK=NULL;
	GByteArray *newV=NULL;
	if (!ctx || !k || !v || !vlen)
		return;
	if (!ctx->extra_headers)
		ctx->extra_headers = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, metautils_gba_clean);
	newK = g_strdup(k);
	newV = g_byte_array_append(g_byte_array_new(), v, vlen);
	g_hash_table_insert( ctx->extra_headers, newK, newV);
}

void
reply_context_add_strheader_in_reply(struct reply_context_s *ctx, const char *k, const gchar *v)
{
	if (!ctx || !k || !v)
		return;
	reply_context_add_bufheader_in_reply(ctx, k, (guint8*)v, strlen(v));
}

void
reply_context_add_header_in_reply(struct reply_context_s *ctx, const char *k, GByteArray *v)
{
	if (!ctx || !k || !v)
		return;
	reply_context_add_bufheader_in_reply(ctx, k, v->data, v->len);
}

static void
reply_ctx_header_adder (gpointer k, gpointer v, gpointer u)
{
	MESSAGE answer = u;
	GByteArray *gba = v;
	if (!k || !v || !u)
		return;
	if (!gba->data || gba->len<=0)
		return;
	metautils_message_add_field (answer, (char*)k, gba->data, gba->len);
}

gint
reply_context_reply (struct reply_context_s *ctx, GError **err)
{
	EXTRA_ASSERT (ctx != NULL);

	register gchar *ptr_msg = ctx->header.msg ? ctx->header.msg : "NOMSG";
	MESSAGE answer = metaXServer_reply_simple (ctx->req_ctx->request, ctx->header.code, ptr_msg);
	if (ctx->extra_headers)
		g_hash_table_foreach(ctx->extra_headers, reply_ctx_header_adder, answer);
	if (ctx->body.buffer && (ctx->body.size > 0))
		metautils_message_set_BODY(answer, ctx->body.buffer, ctx->body.size);

	GByteArray *encoded = message_marshall_gba_and_clean(answer);
	answer = NULL;
	if (encoded) {
		gint _to =  MAX(MS_REPLY_TIMEOUT, MIN(60000, default_to_operation));
		gint sent = sock_to_write(ctx->req_ctx->fd, _to, encoded->data, encoded->len, err);
		gboolean done = (sent > 0) && (encoded->len == (guint)sent);
		g_byte_array_unref (encoded);
		if (!done) {
			g_prefix_error(err, "Failed to reply: ");
			return 0;
		}
	}
	return 1;
}

gint message_handler_add (const char *name,
	message_matcher_f m, message_handler_f h, GError **err)
{
	struct message_handler_s *mh;
	
	if (!name || !m || !h)
	{
		GSETERROR(err,"Invalid parameter");
		return 0;
	}

	mh = g_malloc0 (sizeof(struct message_handler_s));
	mh->matcher = m;
	mh->handler = h;
	strncpy (mh->name, name, SIZE_MSGHANDLERNAME-1);
	mh->next = BEACON_MSGHANDLER.next;
	mh->handler_v2 = NULL;

	BEACON_MSGHANDLER.next = mh;

	DEBUG ("new message handler added : %s", name);
	
	return 1;
}

gint message_handler_add_v2 (const char *name,
	message_matcher_f m, message_handler_v2_f h, const GPtrArray* tags, GError **err)
{
	struct message_handler_s *mh;
	
	if (!name || !m || !h) {
		GSETERROR(err,"Invalid parameters");
		return 0;
	}

	mh = g_malloc0 (sizeof(struct message_handler_s));
	mh->matcher = m;
	mh->handler = NULL;
	strncpy (mh->name, name, SIZE_MSGHANDLERNAME-1);
	mh->next = BEACON_MSGHANDLER.next;
	mh->handler_v2 = h;

	BEACON_MSGHANDLER.next = mh;

	if(!serv_tags)
		serv_tags = g_ptr_array_new();

	/* if the handlers have stats for our service info, load it */
	if (tags) {
		gsize i;
		for (i=0; i<tags->len; i++) {
			struct service_tag_s* tag = g_ptr_array_index(tags, i);
			g_ptr_array_add(serv_tags, service_tag_dup(tag));
		}
	}

	DEBUG ("new message handler added : %s", name);
	
	return 1;
}

void
request_context_clear(struct request_context_s* request_info)
{
	if(request_info == NULL)
		return;
	if(request_info->local_addr)
		g_free(request_info->local_addr);
	if(request_info->remote_addr)
		g_free(request_info->remote_addr);
	memset(request_info, 0, sizeof(struct request_context_s));
}

void
request_context_free(struct request_context_s* request_info)
{
	if(request_info == NULL)
		return;
	request_context_clear(request_info);
	g_free(request_info);
}

void
request_context_gclean(gpointer p1, gpointer p2)
{
	(void) p2;
	if (p1)
		request_context_free((struct request_context_s*)p1);
}

struct request_context_s*
request_context_create(int fd, addr_info_t *fd_peer)
{
	struct request_context_s *ctx;
	struct sockaddr_storage ss_src, ss_dst;
	socklen_t ss_src_len, ss_dst_len;

	ctx = g_malloc0(sizeof(struct request_context_s));
	ctx->fd = fd;
	gettimeofday(&(ctx->tv_start), NULL);

	ctx->remote_addr = g_malloc0(sizeof(addr_info_t));
	memset(&ss_dst, 0, sizeof(ss_dst));
	ss_dst_len = sizeof(ss_dst);
	getpeername(fd, (struct sockaddr*) &ss_dst, &ss_dst_len);
	addrinfo_from_sockaddr(ctx->remote_addr, (struct sockaddr*)&ss_dst, ss_dst_len);

	ctx->local_addr = g_malloc0(sizeof(addr_info_t));
	if (fd_peer)
		memcpy(ctx->local_addr, fd_peer, sizeof(addr_info_t));
	else {
		memset(&ss_src, 0, sizeof(ss_src));
		ss_src_len = sizeof(ss_src);
		getsockname(fd, (struct sockaddr*) &ss_src, &ss_src_len);
		addrinfo_from_sockaddr(ctx->local_addr, (struct sockaddr*)&ss_src, ss_src_len);
	}

	return ctx;
}

gchar*
gridd_get_ns_name(void)
{
	if(!ns_info || !ns_info->name)
		return NULL;
	return g_strdup(ns_info->name);
}

namespace_info_t*
gridd_get_namespace_info(GError **error)
{
	(void) error;
	if(!ns_info)
		return NULL;
	return namespace_info_dup(ns_info);
}

