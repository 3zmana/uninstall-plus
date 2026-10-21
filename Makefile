# Build and install Uninstall Plus.
#   make install DESTDIR=/tmp/root   install into a staging directory
#   make test                         run the unit tests
#   make locale                       compile translations into build/locale
#   make pot                          refresh po/uninstall-plus.pot from the sources
#   make tarball                      create the source archive used by the spec
#   make rpm                          build the RPM in ./rpmbuild (Fedora)

NAME     := uninstall-plus
VERSION  := 1.0.1
APP_ID   := io.github._3zmana.UninstallPlus
PREFIX   ?= /usr
DATADIR  ?= $(PREFIX)/share
LIBEXEC  ?= $(PREFIX)/libexec
BINDIR   ?= $(PREFIX)/bin
DESTDIR  ?=

PYFILES  := $(wildcard uninstall_plus/*.py)
LINGUAS  := $(shell cat po/LINGUAS)
MOFILES  := $(foreach lang,$(LINGUAS),build/locale/$(lang)/LC_MESSAGES/$(NAME).mo)

.PHONY: all locale pot install uninstall test tarball rpm clean

all: locale

locale: $(MOFILES)

build/locale/%/LC_MESSAGES/$(NAME).mo: po/%.po
	mkdir -p $(dir $@)
	msgfmt --check -o $@ $<

pot:
	xgettext --language=Python --from-code=UTF-8 --keyword=_ --keyword=N_ \
	    --keyword=ngettext:1,2 --add-comments=Translators --package-name=$(NAME) \
	    --package-version=$(VERSION) \
	    --msgid-bugs-address=https://github.com/3zmana/uninstall-plus/issues -o po/$(NAME).pot $(PYFILES) helper/uninstall-plus-helper
	for lang in $(LINGUAS); do msgmerge --update --backup=none po/$$lang.po po/$(NAME).pot; done

install: locale
	install -Dm755 bin/uninstall-plus $(DESTDIR)$(BINDIR)/uninstall-plus
	install -Dm755 helper/uninstall-plus-helper $(DESTDIR)$(LIBEXEC)/uninstall-plus/uninstall-plus-helper
	install -d $(DESTDIR)$(DATADIR)/uninstall-plus/uninstall_plus
	install -m644 $(PYFILES) $(DESTDIR)$(DATADIR)/uninstall-plus/uninstall_plus/
	install -Dm644 data/$(APP_ID).desktop $(DESTDIR)$(DATADIR)/applications/$(APP_ID).desktop
	install -Dm644 data/$(APP_ID).metainfo.xml $(DESTDIR)$(DATADIR)/metainfo/$(APP_ID).metainfo.xml
	install -Dm644 data/$(APP_ID).svg $(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/$(APP_ID).svg
	install -Dm644 data/$(APP_ID)-symbolic.svg $(DESTDIR)$(DATADIR)/icons/hicolor/symbolic/apps/$(APP_ID)-symbolic.svg
	install -Dm644 data/$(APP_ID).policy $(DESTDIR)$(DATADIR)/polkit-1/actions/$(APP_ID).policy
	for lang in $(LINGUAS); do \
	    install -Dm644 build/locale/$$lang/LC_MESSAGES/$(NAME).mo \
	        $(DESTDIR)$(DATADIR)/locale/$$lang/LC_MESSAGES/$(NAME).mo; \
	done

uninstall:
	rm -f $(DESTDIR)$(BINDIR)/uninstall-plus
	rm -rf $(DESTDIR)$(LIBEXEC)/uninstall-plus $(DESTDIR)$(DATADIR)/uninstall-plus
	rm -f $(DESTDIR)$(DATADIR)/applications/$(APP_ID).desktop
	rm -f $(DESTDIR)$(DATADIR)/metainfo/$(APP_ID).metainfo.xml
	rm -f $(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/$(APP_ID).svg
	rm -f $(DESTDIR)$(DATADIR)/icons/hicolor/symbolic/apps/$(APP_ID)-symbolic.svg
	rm -f $(DESTDIR)$(DATADIR)/polkit-1/actions/$(APP_ID).policy
	rm -f $(DESTDIR)$(DATADIR)/locale/*/LC_MESSAGES/$(NAME).mo

test: locale
	python3 -m unittest discover -s tests -v

tarball:
	mkdir -p dist
	git archive --format=tar.gz --prefix=$(NAME)-$(VERSION)/ -o dist/$(NAME)-$(VERSION).tar.gz HEAD 2>/dev/null || \
	tar --exclude=./dist --exclude=./rpmbuild --exclude=./build --exclude='__pycache__' \
	    --transform 's,^\.,$(NAME)-$(VERSION),' -czf dist/$(NAME)-$(VERSION).tar.gz .

rpm: tarball
	mkdir -p rpmbuild/SOURCES
	cp dist/$(NAME)-$(VERSION).tar.gz rpmbuild/SOURCES/
	rpmbuild --define "_topdir $(CURDIR)/rpmbuild" -ba $(NAME).spec
	@echo "RPM files are in rpmbuild/RPMS/noarch and rpmbuild/SRPMS."

clean:
	rm -rf build dist rpmbuild tests/__pycache__ uninstall_plus/__pycache__
